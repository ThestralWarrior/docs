// Loader: LoweredScene JSON → Three.js scene. The JavaScript half of the compiler.
//
// It only draws what lowering produced: no geometry decisions live here, so the
// validators (which read the same lowered scene) always measure what you see.
// Every Three.js object keeps the IR node ID in userData.node, for highlights and picking.

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Sky } from "three/addons/objects/Sky.js";
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

export const PINNED_THREE = "0.186.1";

const TONE_MAPPING = {
  none: THREE.NoToneMapping,
  linear: THREE.LinearToneMapping,
  reinhard: THREE.ReinhardToneMapping,
  cineon: THREE.CineonToneMapping,
  aces: THREE.ACESFilmicToneMapping,
  agx: THREE.AgXToneMapping,
  neutral: THREE.NeutralToneMapping,
};
const FRONT_FIX = { "+z": 0, "-z": Math.PI, "+x": -Math.PI / 2, "-x": Math.PI / 2 };
const ZONE_COLORS = { clearance: 0xf2a33a, walkway: 0x3aa0f2, no_build: 0xe5484d };
const HIGHLIGHT = 0xff3b30;

function applyMatrix(object, matrix) {
  new THREE.Matrix4().fromArray(matrix).decompose(object.position, object.quaternion, object.scale);
}

function shapeGeometry(shape, [w, h, d], segments) {
  let g;
  switch (shape) {
    case "box":
      g = new THREE.BoxGeometry(w, h, d);
      break;
    case "sphere":
      g = new THREE.SphereGeometry(0.5, segments ?? 32, Math.max(3, Math.round((segments ?? 32) / 2))).scale(w, h, d);
      break;
    case "cylinder":
      g = new THREE.CylinderGeometry(0.5, 0.5, 1, segments ?? 48).scale(w, h, d);
      break;
    case "cone":
      g = new THREE.ConeGeometry(0.5, 1, segments ?? 48).scale(w, h, d);
      break;
    case "capsule":
      g = new THREE.CapsuleGeometry(0.25, 0.5, 8, 24).scale(w * 2, h, d * 2);
      break;
    case "torus":
      g = new THREE.TorusGeometry(0.4, 0.1, 16, 48).rotateX(Math.PI / 2).scale(w, h * 5, d);
      break;
    case "plane":
      return new THREE.PlaneGeometry(w, d).rotateX(-Math.PI / 2);
    case "wedge": {
      const profile = new THREE.Shape([new THREE.Vector2(-d / 2, 0), new THREE.Vector2(d / 2, 0), new THREE.Vector2(-d / 2, h)]);
      return new THREE.ExtrudeGeometry(profile, { depth: w, bevelEnabled: false }).rotateY(Math.PI / 2).translate(-w / 2, 0, 0);
    }
    default:
      g = new THREE.BoxGeometry(w, h, d);
  }
  return g.translate(0, h / 2, 0); // origin at the centre of the bottom face, like the IR
}

function slabGeometry(polygon, thickness) {
  const shape = new THREE.Shape(polygon.map(([x, z]) => new THREE.Vector2(x, z)));
  // Shape lives in XY; rotating +90° about X sends shape y to world z and the extrusion downward.
  return new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false }).rotateX(Math.PI / 2);
}

export class WorldView extends EventTarget {
  // assetData: optional map of asset URI → GLB bytes (ArrayBuffer or base64), for pages that cannot serve .glb files.
  constructor(container, { assetBase = "./", assetData = null, debug = false } = {}) {
    super();
    this.container = container;
    this.assetBase = assetBase;
    this.assetData = assetData;
    this.options = { cutaway: true, zones: debug, boxes: debug, ceiling: false };
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap; // r186 removed PCFSoftShadowMap
    container.appendChild(this.renderer.domElement);
    this.orbitCamera = new THREE.PerspectiveCamera(50, 1, 0.05, 2000);
    this.camera = this.orbitCamera;
    this.controls = new OrbitControls(this.orbitCamera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.gltf = new GLTFLoader();
    this.glbCache = new Map();
    this.tweens = [];
    this.fades = [];
    this.labels = [];
    this.warnings = [];
    this.behaviors = [];
    this.labelLayer = Object.assign(document.createElement("div"), { className: "wv-labels" });
    this.labelLayer.style.cssText = "position:absolute;inset:0;pointer-events:none;overflow:hidden";
    if (getComputedStyle(container).position === "static") container.style.position = "relative";
    container.appendChild(this.labelLayer);
    this.startTime = performance.now();
    this.fixedTime = null;
    RectAreaLightUniformsLib.init();
    new ResizeObserver(() => this.resize()).observe(container);
    this.renderer.domElement.addEventListener("click", (e) => this.pick(e));
    this.renderer.setAnimationLoop(() => this.frame());
  }

  // Loading -------------------------------------------------------------------

  async load(lowered) {
    this.lowered = lowered;
    this.warnings = [];
    const pinned = lowered.versions?.three;
    if (pinned && pinned.split(".")[1] !== THREE.REVISION) {
      this.warn(`scene was lowered for three ${pinned}, but this page runs r${THREE.REVISION}`);
    }
    this.scene = new THREE.Scene();
    this.root = new THREE.Group();
    this.debugBoxes = new THREE.Group();
    this.zones = new THREE.Group();
    this.highlights = new THREE.Group();
    this.marks = new THREE.Group();
    this.scene.add(this.root, this.debugBoxes, this.zones, this.highlights, this.marks);
    this.clearLabels();
    this.byItem = new Map();
    this.cameras = new Map();
    this.materials = new Map(Object.entries(lowered.materials).map(([id, m]) => [id, this.makeMaterial(m)]));
    this.skyGroup = null;
    this.applyEnvironment(lowered.environment, lowered.bounds, lowered.world_id);
    await Promise.all(lowered.items.map((item) => this.addItem(item)));
    this.setupBehaviors(lowered.behaviors ?? []);
    this.setupPost(lowered.environment.post);
    this.applyOptions();
    this.frameAll();
    this.dispatchEvent(new CustomEvent("loaded", { detail: { items: lowered.items.length, warnings: this.warnings } }));
  }

  makeMaterial(m) {
    const params = {
      color: new THREE.Color(m.base_color),
      metalness: m.metallic,
      roughness: m.roughness,
      side: m.double_sided ? THREE.DoubleSide : THREE.FrontSide,
    };
    if (m.emissive) Object.assign(params, { emissive: new THREE.Color(m.emissive), emissiveIntensity: m.emissive_intensity });
    if (m.transmission > 0) {
      return new THREE.MeshPhysicalMaterial({ ...params, transmission: m.transmission, ior: m.ior, transparent: true, opacity: Math.max(m.opacity, 0.35) });
    }
    if (m.opacity < 1) Object.assign(params, { transparent: true, opacity: m.opacity });
    return new THREE.MeshStandardMaterial(params);
  }

  applyEnvironment(env, bounds, seedText = "") {
    const r = this.renderer;
    r.toneMapping = TONE_MAPPING[env.tone_mapping] ?? THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = env.exposure;
    const size = bounds ? Math.max(...bounds[1].map((v, i) => v - bounds[0][i]), 4) : 20;
    const center = bounds ? new THREE.Vector3(...bounds[0]).add(new THREE.Vector3(...bounds[1])).multiplyScalar(0.5) : new THREE.Vector3();
    const sky = env.sky;
    if (sky.type === "color") {
      this.scene.background = new THREE.Color(sky.color);
    } else if (sky.type === "gradient" || sky.type === "hdri") {
      if (sky.type === "hdri") this.warn("HDRI skies are not loaded yet; drawing a gradient instead");
      const canvas = Object.assign(document.createElement("canvas"), { width: 2, height: 256 });
      const ctx = canvas.getContext("2d");
      const grad = ctx.createLinearGradient(0, 0, 0, 256);
      grad.addColorStop(0, sky.zenith ?? "#6a9bd1");
      grad.addColorStop(0.55, sky.horizon ?? "#dfe9f2");
      grad.addColorStop(1, sky.ground ?? "#8a8478");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, 2, 256);
      const tex = new THREE.CanvasTexture(canvas);
      tex.colorSpace = THREE.SRGBColorSpace;
      this.scene.background = tex;
    } else if (sky.type === "procedural") {
      const s = new Sky();
      s.scale.setScalar(5000);
      const u = s.material.uniforms;
      u.turbidity.value = sky.turbidity;
      u.rayleigh.value = sky.rayleigh;
      u.mieCoefficient.value = sky.mie_coefficient;
      u.mieDirectionalG.value = sky.mie_directional_g;
      const sun = env.sun ?? { elevation_deg: 45, azimuth_deg: 135 };
      u.sunPosition.value.copy(this.sunDirection(sun));
      this.scene.add(s);
    } else if (sky.type === "space") {
      this.scene.background = new THREE.Color(sky.zenith);
      this.skyGroup = this.makeSpaceSky(sky, seedText);
      this.scene.add(this.skyGroup);
    }
    this.scene.environment = null;
    if (env.reflections === "studio") {
      const pmrem = new THREE.PMREMGenerator(this.renderer);
      this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      this.scene.environmentIntensity = env.reflection_intensity ?? 1;
      pmrem.dispose();
    }
    const amb = env.ambient;
    this.scene.add(
      amb.type === "hemisphere"
        ? new THREE.HemisphereLight(new THREE.Color(amb.color), new THREE.Color(amb.ground_color), amb.intensity)
        : new THREE.AmbientLight(new THREE.Color(amb.color), amb.intensity),
    );
    if (env.sun) {
      const sun = env.sun;
      const light = new THREE.DirectionalLight(sun.color ? new THREE.Color(sun.color) : kelvinColor(sun.kelvin), sun.intensity_lux);
      light.position.copy(center).addScaledVector(this.sunDirection(sun), size * 2);
      light.target.position.copy(center);
      light.castShadow = sun.cast_shadows;
      light.shadow.mapSize.set(sun.shadow_map_size, sun.shadow_map_size);
      Object.assign(light.shadow.camera, { left: -size, right: size, top: size, bottom: -size, near: 0.1, far: size * 5 });
      light.shadow.bias = -0.0005;
      this.scene.add(light, light.target);
    }
    if (env.fog) {
      this.scene.fog =
        env.fog.type === "linear"
          ? new THREE.Fog(new THREE.Color(env.fog.color), env.fog.near, env.fog.far)
          : new THREE.FogExp2(new THREE.Color(env.fog.color), env.fog.density);
    }
  }

  /** Gradient dome, stars and distant bodies. It follows the camera, so it always looks infinitely far away. */
  makeSpaceSky(sky, seedText) {
    const group = new THREE.Group();
    const random = seededRandom(seedText);
    const dome = new THREE.SphereGeometry(1, 48, 24);
    const zenith = new THREE.Color(sky.zenith);
    const horizon = new THREE.Color(sky.horizon);
    const colors = [];
    for (let i = 0; i < dome.attributes.position.count; i++) {
      const y = dome.attributes.position.getY(i);
      const c = y < 0 ? horizon.clone().multiplyScalar(0.55) : horizon.clone().lerp(zenith, Math.pow(y, 0.45));
      colors.push(c.r, c.g, c.b);
    }
    dome.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    const backdrop = new THREE.Mesh(dome, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide, fog: false, depthWrite: false }));
    backdrop.renderOrder = -3;
    group.add(backdrop);

    // Stars: many faint ones, a few bright enough to bloom, and an optional dense band.
    const tints = [new THREE.Color("#ffffff"), new THREE.Color("#cfe0ff"), new THREE.Color("#ffe6c4"), new THREE.Color("#ffd2d2")];
    const layers = [
      { size: 1.3, share: 0.85, gain: 0.7 },
      { size: 2.4, share: 0.15, gain: 2.2 },
    ];
    const direction = () => {
      const u = random() * 2 - 1, a = random() * Math.PI * 2, r = Math.sqrt(1 - u * u);
      return new THREE.Vector3(r * Math.cos(a), u, r * Math.sin(a));
    };
    const band = sky.milky_way
      ? new THREE.Quaternion().setFromEuler(new THREE.Euler(THREE.MathUtils.degToRad(sky.milky_way[1]), THREE.MathUtils.degToRad(sky.milky_way[0]), 0, "YXZ"))
      : null;
    for (const layer of layers) {
      const count = Math.round(sky.stars * layer.share);
      const bandCount = band ? Math.round(count * 0.8) : 0;
      const positions = [], starColors = [];
      for (let i = 0; i < count + bandCount; i++) {
        let d;
        if (i < count) d = direction();
        else {
          const a = random() * Math.PI * 2;
          const spread = (random() + random() + random() - 1.5) * 0.18;
          d = new THREE.Vector3(Math.cos(a), spread, Math.sin(a)).normalize().applyQuaternion(band);
        }
        if (d.y < -0.08) continue;
        positions.push(d.x * 0.97, d.y * 0.97, d.z * 0.97);
        const level = sky.star_brightness * layer.gain * (0.25 + 0.75 * random() ** 3) * (i >= count ? 0.6 : 1);
        const tint = tints[Math.floor(random() * tints.length)];
        starColors.push(tint.r * level, tint.g * level, tint.b * level);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.Float32BufferAttribute(starColors, 3));
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: layer.size, sizeAttenuation: false, vertexColors: true, fog: false, depthWrite: false }));
      points.renderOrder = -2;
      group.add(points);
    }

    for (const body of sky.bodies ?? []) group.add(this.makeBody(body, random));
    group.traverse((o) => (o.frustumCulled = false));
    return group;
  }

  makeBody(body, random) {
    const distance = 0.9;
    const radius = distance * Math.tan(THREE.MathUtils.degToRad(body.angular_size_deg / 2));
    const holder = new THREE.Group();
    holder.position.copy(this.sunDirection(body)).multiplyScalar(distance);
    holder.lookAt(0, 0, 0);
    const texture = bodyTexture(body, random);
    const material =
      body.style === "star"
        ? new THREE.MeshBasicMaterial({ color: new THREE.Color(body.color).multiplyScalar(Math.max(1, body.glow)), fog: false })
        : new THREE.MeshStandardMaterial({
            color: 0xffffff,
            map: texture,
            roughness: 1,
            metalness: 0,
            fog: false,
            emissive: new THREE.Color(body.color),
            emissiveIntensity: body.glow,
          });
    const sphere = new THREE.Mesh(new THREE.SphereGeometry(radius, 64, 32), material);
    sphere.rotation.set(0.35, 0, 0.25);
    sphere.renderOrder = -1;
    holder.add(sphere);
    if (body.ring) {
      const r = body.ring;
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(radius * r.inner, radius * r.outer, 128, 1),
        new THREE.MeshBasicMaterial({ color: new THREE.Color(r.color).multiplyScalar(0.6), side: THREE.DoubleSide, transparent: true, opacity: r.opacity, fog: false, depthWrite: false }),
      );
      ring.rotation.x = -THREE.MathUtils.degToRad(90 - r.tilt_deg); // near side of the ring below the body
      ring.renderOrder = -1;
      holder.add(ring);
    }
    return holder;
  }

  setupPost(post) {
    this.composer?.dispose();
    this.composer = null;
    const bloom = post?.bloom;
    if (!bloom) return;
    const size = this.renderer.getSize(new THREE.Vector2());
    this.composer = new EffectComposer(this.renderer);
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(size.x, size.y);
    this.renderPass = new RenderPass(this.scene, this.camera);
    this.composer.addPass(this.renderPass);
    this.composer.addPass(new UnrealBloomPass(size, bloom.strength, bloom.radius, bloom.threshold));
    this.composer.addPass(new OutputPass());
  }

  // Behaviours ------------------------------------------------------------------

  /** Animations from lowering. Items keep their rest matrix; each frame applies the behaviours on top of it. */
  setupBehaviors(list) {
    this.behaviors = [];
    for (const b of list) {
      const objects = b.items.map((id) => this.byItem.get(id)).filter(Boolean);
      if (!objects.length) continue;
      for (const o of objects) o.userData.rest ??= new THREE.Matrix4().compose(o.position, o.quaternion, o.scale);
      const origin = new THREE.Matrix4().fromArray(b.origin);
      const entry = { ...b, objects, origin, originInverse: origin.clone().invert(), pivot: new THREE.Vector3().setFromMatrixPosition(origin) };
      if (b.preset === "follow_path") {
        const points = b.path.map((p) => new THREE.Vector3(...p));
        if (b.closed) points.push(points[0].clone());
        const lengths = [0];
        for (let i = 1; i < points.length; i++) lengths.push(lengths[i - 1] + points[i].distanceTo(points[i - 1]));
        Object.assign(entry, { points, lengths, total: lengths[lengths.length - 1] });
      }
      if (b.preset === "flicker") entry.base = objects.map((o) => (o.isLight ? o.intensity : 0));
      this.behaviors.push(entry);
    }
  }

  /** Seconds since load, or the time set with setTime() for repeatable screenshots. */
  time() {
    return this.fixedTime ?? (performance.now() - this.startTime) / 1000;
  }

  setTime(seconds) {
    this.fixedTime = seconds;
  }

  animate(t) {
    const combined = new Map();
    for (const b of this.behaviors) {
      if (b.preset === "flicker") {
        const amount = b.params.amount ?? 0.3, speed = b.params.speed ?? 8;
        b.objects.forEach((o, i) => {
          if (!o.isLight) return;
          const wobble = 0.5 + 0.25 * Math.sin(t * speed + i * 1.7) + 0.25 * Math.sin(t * speed * 2.3 + i * 4.1);
          o.intensity = b.base[i] * (1 - amount * wobble);
        });
        continue;
      }
      const d = this.behaviorMatrix(b, t);
      for (const o of b.objects) combined.set(o, d.clone().multiply(combined.get(o) ?? new THREE.Matrix4()));
    }
    for (const [o, d] of combined) d.multiply(o.userData.rest).decompose(o.position, o.quaternion, o.scale);
  }

  behaviorMatrix(b, t) {
    const p = b.params;
    const about = (axisName, degrees) => {
      const local = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }[axisName] ?? [0, 1, 0];
      const axis = new THREE.Vector3(...local).transformDirection(b.origin);
      return new THREE.Matrix4()
        .makeTranslation(b.pivot.x, b.pivot.y, b.pivot.z)
        .multiply(new THREE.Matrix4().makeRotationAxis(axis, THREE.MathUtils.degToRad(degrees)))
        .multiply(new THREE.Matrix4().makeTranslation(-b.pivot.x, -b.pivot.y, -b.pivot.z));
    };
    const wave = (period, phase = 0) => Math.sin((2 * Math.PI * t) / Math.max(period, 0.05) + phase * 2 * Math.PI);
    if (b.preset === "spin") return about(p.axis ?? "y", (p.speed_deg_s ?? 30) * t);
    if (b.preset === "sway") return about(p.axis ?? "z", (p.angle_deg ?? 4) * wave(p.period_s ?? 4, p.phase ?? 0));
    if (b.preset === "bob") return new THREE.Matrix4().makeTranslation(0, (p.amplitude_m ?? 0.25) * wave(p.period_s ?? 3, p.phase ?? 0), 0);
    if (b.preset === "follow_path") {
      let s = (p.offset_m ?? 0) + (p.speed_m_s ?? 2) * t;
      let ahead = 1;
      if (b.closed) s = ((s % b.total) + b.total) % b.total;
      else {
        s = ((s % (2 * b.total)) + 2 * b.total) % (2 * b.total);
        if (s > b.total) (s = 2 * b.total - s), (ahead = -1);
      }
      const here = this.pointAt(b, s);
      const next = this.pointAt(b, b.closed ? (s + ahead * 0.5 + b.total) % b.total : THREE.MathUtils.clamp(s + ahead * 0.5, 0, b.total));
      if (next.distanceTo(here) < 1e-6) return new THREE.Matrix4();
      const pose = new THREE.Matrix4().lookAt(next, here, new THREE.Vector3(0, 1, 0)).setPosition(here);
      return pose.multiply(b.originInverse);
    }
    return new THREE.Matrix4();
  }

  pointAt(b, s) {
    const { points, lengths } = b;
    let i = 1;
    while (i < lengths.length - 1 && lengths[i] < s) i++;
    const span = lengths[i] - lengths[i - 1] || 1;
    return points[i - 1].clone().lerp(points[i], (s - lengths[i - 1]) / span);
  }

  sunDirection({ elevation_deg, azimuth_deg }) {
    const el = THREE.MathUtils.degToRad(elevation_deg);
    const az = THREE.MathUtils.degToRad(azimuth_deg);
    return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  }

  async addItem(item, { appear = 0 } = {}) {
    let object;
    if (item.type === "asset") object = await this.makeAsset(item);
    else if (item.type === "shape") object = new THREE.Mesh(shapeGeometry(item.shape, item.size, item.segments), this.material(item.material, { flat: item.flat }));
    else if (item.type === "slab") object = new THREE.Mesh(slabGeometry(item.polygon, item.thickness), this.material(item.material));
    else if (item.type === "heightfield") object = this.makeHeightfield(item);
    else if (item.type === "mesh") object = this.makeMesh(item);
    else if (item.type === "instances") object = await this.makeInstances(item);
    else if (item.type === "light") object = this.makeLight(item);
    else if (item.type === "camera") {
      this.cameras.set(item.id, item);
      return;
    } else if (item.type === "zone") {
      const zone = this.makeZone(item);
      zone.userData.item = item.id;
      this.zones.add(zone);
      return;
    }
    if (!object) return;
    applyMatrix(object, item.matrix);
    if (appear && !object.isLight) {
      // Grow in from nothing at its final place.
      const to = new THREE.Matrix4().fromArray(item.matrix);
      const from = to.clone().multiply(new THREE.Matrix4().makeScale(0.001, 0.001, 0.001));
      from.decompose(object.position, object.quaternion, object.scale);
      this.tween(object, from, to, appear);
    }
    object.visible = item.visible !== false;
    object.userData = { node: item.node, item: item.id, role: item.role, facing: item.facing };
    object.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = !["rug", "window", "water", "path", "terrain", "flowers"].includes(item.role) && !o.userData.noCast;
        o.receiveShadow = true;
        o.userData.node = item.node;
        o.userData.item = item.id;
      }
    });
    this.root.add(object);
    this.byItem.set(item.id, object);
    const size = this.itemSize(item);
    if (size) {
      const box = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(...size).translate(0, size[1] / 2, 0)),
        new THREE.LineBasicMaterial({ color: 0x2bb3c0 }),
      );
      applyMatrix(box, item.matrix);
      box.userData.item = item.id;
      this.debugBoxes.add(box);
    }
  }

  /** The box the validators measure: asset dims or shape size. Lights and cameras have none. */
  itemSize(item) {
    if (item.type === "asset") return this.lowered.assets[item.asset].dims;
    if (item.type === "shape") return item.size;
    return null;
  }

  material(id, { flat = false, doubleSided = false } = {}) {
    const m = this.materials.get(id);
    if (!m) {
      this.warn(`unknown material '${id}'`);
      return new THREE.MeshStandardMaterial({ color: 0xff00ff });
    }
    if (!flat && !doubleSided) return m;
    const key = `${id}|${flat}|${doubleSided}`;
    if (!this.materials.has(key)) {
      const variant = m.clone();
      variant.flatShading = flat;
      if (doubleSided) variant.side = THREE.DoubleSide;
      this.materials.set(key, variant);
    }
    return this.materials.get(key);
  }

  makeHeightfield(item) {
    const { rows, cols, size, heights, layers, layer_materials: layerMaterials } = item;
    const palette = layerMaterials.map((id) => new THREE.Color(this.lowered.materials[id]?.base_color ?? "#6f8a3a"));
    const positions = new Float32Array(rows * cols * 3);
    const colors = new Float32Array(rows * cols * 3);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const i = r * cols + c;
        positions.set([-size[0] / 2 + (size[0] * c) / (cols - 1), heights[i], -size[1] / 2 + (size[1] * r) / (rows - 1)], i * 3);
        const base = palette[layers.length ? layers[i] : 0] ?? palette[0];
        const shade = 0.94 + 0.12 * (((i * 2654435761) >>> 0) / 4294967296); // gentle per-vertex variation
        colors.set([base.r * shade, base.g * shade, base.b * shade], i * 3);
      }
    }
    const index = [];
    for (let r = 0; r < rows - 1; r++) {
      for (let c = 0; c < cols - 1; c++) {
        const a = r * cols + c, b = a + 1, below = a + cols, diag = below + 1;
        index.push(a, below, b, b, below, diag);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.setIndex(index);
    const faceted = geometry.toNonIndexed();
    faceted.computeVertexNormals();
    const mesh = new THREE.Mesh(faceted, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, flatShading: true }));
    mesh.userData.noCast = true;
    return mesh;
  }

  makeMesh(item) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(item.positions, 3));
    geometry.setIndex(item.indices);
    const final = item.flat ? geometry.toNonIndexed() : geometry;
    final.computeVertexNormals();
    return new THREE.Mesh(final, this.material(item.material, { flat: item.flat, doubleSided: item.double_sided }));
  }

  /** Many copies as InstancedMesh: one per sub-mesh of the asset, or one for a shape. */
  async makeInstances(item) {
    const group = new THREE.Group();
    const count = item.transforms.length;
    const placements = item.transforms.map(([x, y, z, yaw, s]) =>
      new THREE.Matrix4().compose(
        new THREE.Vector3(x, y, z),
        new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(yaw)),
        new THREE.Vector3(s, s, s),
      ),
    );
    const parts = [];
    if (item.asset) {
      const template = await this.makeAsset({ asset: item.asset, materials: item.materials ?? {} });
      template.updateMatrixWorld(true);
      template.traverse((o) => o.isMesh && parts.push({ geometry: o.geometry, material: o.material, local: o.matrixWorld.clone() }));
    } else {
      parts.push({
        geometry: shapeGeometry(item.shape, item.size, item.segments),
        material: this.material(item.material, { flat: item.flat }),
        local: new THREE.Matrix4(),
      });
    }
    const tall = item.asset ? this.lowered.assets[item.asset].dims[1] > 1.4 : item.size?.[1] > 1.4;
    for (const part of parts) {
      const mesh = new THREE.InstancedMesh(part.geometry, part.material, count);
      const m = new THREE.Matrix4();
      placements.forEach((p, i) => mesh.setMatrixAt(i, m.multiplyMatrices(p, part.local)));
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
      mesh.castShadow = tall;
      mesh.receiveShadow = true;
      mesh.userData.noCast = !tall;
      group.add(mesh);
    }
    return group;
  }

  async glb(uri) {
    if (!this.glbCache.has(uri)) {
      const embedded = this.assetData?.[uri];
      this.glbCache.set(
        uri,
        embedded
          ? this.gltf.parseAsync(toArrayBuffer(embedded), "")
          : this.gltf.loadAsync(new URL(uri, new URL(this.assetBase, location.href)).href),
      );
    }
    return this.glbCache.get(uri);
  }

  async makeAsset(item) {
    const asset = this.lowered.assets[item.asset];
    const [w, h, d] = asset.dims;
    const outer = new THREE.Group();
    try {
      const gltf = await this.glb(asset.uri);
      const model = gltf.scene.clone(true);
      // Normalise: turn the front to +Z, put the bottom centre at the origin, fit to dims.
      const turn = new THREE.Group();
      turn.rotation.y = FRONT_FIX[asset.front] ?? 0;
      turn.add(model);
      turn.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(turn);
      const size = box.getSize(new THREE.Vector3());
      const fit = new THREE.Group();
      fit.scale.set(w / size.x, h / size.y, d / size.z);
      model.position.sub(new THREE.Vector3((box.min.x + box.max.x) / 2, box.min.y, (box.min.z + box.max.z) / 2).applyAxisAngle(new THREE.Vector3(0, 1, 0), -turn.rotation.y));
      fit.add(turn);
      outer.add(fit);
      const ratio = [w / size.x, h / size.y, d / size.z];
      if (Math.max(...ratio) / Math.min(...ratio) > 1.05) this.warn(`asset '${item.asset}' is stretched to fit its dims`);
      const overrides = item.materials ?? {};
      model.traverse((o) => {
        if (!o.isMesh) return;
        const slot = overrides[o.material?.name] ?? overrides["*"];
        if (slot) o.material = this.material(slot);
      });
    } catch (err) {
      this.warn(`could not load ${asset.uri}; drawing a box instead`);
      outer.add(new THREE.Mesh(shapeGeometry("box", asset.dims), new THREE.MeshStandardMaterial({ color: 0xb8b0a4, roughness: 0.9 })));
    }
    return outer;
  }

  makeLight(item) {
    const color = new THREE.Color(item.color);
    let light;
    if (item.light === "point") light = new THREE.PointLight(color, item.intensity, item.range, 2);
    else if (item.light === "spot") light = new THREE.SpotLight(color, item.intensity, item.range, THREE.MathUtils.degToRad(item.angle_deg), item.penumbra, 2);
    else if (item.light === "area") light = new THREE.RectAreaLight(color, item.intensity, item.size[0], item.size[1]);
    else light = new THREE.DirectionalLight(color, item.intensity);
    light.castShadow = !!item.cast_shadows && item.light !== "area";
    if (light.castShadow) light.shadow.bias = -0.0005;
    if (item.target && light.target) {
      light.target.position.set(...item.target);
      this.scene.add(light.target);
    }
    return light;
  }

  makeZone(item) {
    const color = ZONE_COLORS[item.purpose] ?? 0x9b8afb;
    const shape = new THREE.Shape(item.polygon.map(([x, z]) => new THREE.Vector2(x, z)));
    const fill = new THREE.Mesh(
      new THREE.ShapeGeometry(shape).rotateX(Math.PI / 2),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.28, side: THREE.DoubleSide, depthWrite: false }),
    );
    const points = item.polygon.map(([x, z]) => new THREE.Vector3(x, 0, z));
    const edge = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color }));
    const group = new THREE.Group();
    group.add(fill, edge);
    group.position.y = item.y_range[0] + 0.015;
    group.userData = { node: item.node, item: item.id };
    return group;
  }

  // Updates, highlights, options ------------------------------------------------

  /** Moves items to a new lowered scene, animating matrix changes. Used for repair steps and replays. */
  async update(lowered, { duration = 700 } = {}) {
    const before = new Map(this.lowered.items.map((i) => [i.id, i]));
    const after = new Map(lowered.items.map((i) => [i.id, i]));
    const structural = [...after.keys()].some((id) => !before.has(id)) || [...before.keys()].some((id) => !after.has(id));
    if (structural) return this.load(lowered);
    this.lowered = lowered;
    for (const b of this.behaviors) for (const o of b.objects) o.userData.rest = new THREE.Matrix4().fromArray(after.get(o.userData.item).matrix);
    for (const [id, item] of after) {
      const old = before.get(id);
      if (!old || old.matrix.every((v, i) => Math.abs(v - item.matrix[i]) < 1e-6)) continue;
      const from = new THREE.Matrix4().fromArray(old.matrix);
      const to = new THREE.Matrix4().fromArray(item.matrix);
      for (const target of [this.byItem.get(id), ...this.debugBoxes.children.filter((b) => b.userData.item === id)]) {
        if (target) this.tween(target, from, to, duration);
      }
    }
  }

  tween(object, from, to, duration, onDone = null) {
    const p0 = new THREE.Vector3(), q0 = new THREE.Quaternion(), s0 = new THREE.Vector3();
    const p1 = new THREE.Vector3(), q1 = new THREE.Quaternion(), s1 = new THREE.Vector3();
    from.decompose(p0, q0, s0);
    to.decompose(p1, q1, s1);
    this.tweens = this.tweens.filter((t) => t.object !== object);
    this.tweens.push({ object, p0, q0, s0, p1, q1, s1, start: performance.now(), duration, onDone });
  }

  // Patches: update a loaded scene in place, with animation --------------------------

  /** Applies a scene patch from lowering: moves, grows in, shrinks out and recolours instead of reloading. */
  async applyPatch(patch, { duration = 700 } = {}) {
    const next = patchScene(this.lowered, patch);
    if (patch.reload || patch.behaviors) return this.load(next);
    const before = new Map(this.lowered.items.map((i) => [i.id, i]));
    this.lowered = next;
    for (const [id, m] of Object.entries(patch.materials ?? {})) {
      if (this.materials.has(id)) this.fadeMaterial(id, m, duration);
      else this.materials.set(id, this.makeMaterial(m));
    }
    const jobs = [];
    for (const id of patch.removed ?? []) this.removeItem(id, duration);
    for (const item of patch.changed ?? []) {
      const old = before.get(item.id);
      if (old && samePlacementOnly(old, item)) {
        const target = this.byItem.get(item.id);
        const from = new THREE.Matrix4().fromArray(old.matrix);
        const to = new THREE.Matrix4().fromArray(item.matrix);
        for (const o of [target, ...this.debugBoxes.children.filter((b) => b.userData.item === item.id)]) {
          if (o && !old.matrix.every((v, i) => Math.abs(v - item.matrix[i]) < 1e-6)) this.tween(o, from, to, duration);
        }
        if (target) target.visible = item.visible !== false;
        if (item.type === "camera") this.cameras.set(item.id, item);
      } else {
        this.removeItem(item.id, duration);
        jobs.push(this.addItem(item, { appear: duration }));
      }
    }
    for (const item of patch.added ?? []) jobs.push(this.addItem(item, { appear: duration }));
    await Promise.all(jobs);
    const byId = new Map(this.lowered.items.map((i) => [i.id, i]));
    for (const b of this.behaviors) {
      for (const o of b.objects) {
        const item = byId.get(o.userData.item);
        if (item) o.userData.rest = new THREE.Matrix4().fromArray(item.matrix);
      }
    }
    await new Promise((resolve) => setTimeout(resolve, duration));
  }

  /** Shrinks an item away and removes it. */
  removeItem(id, duration = 0) {
    const object = this.byItem.get(id);
    this.byItem.delete(id);
    this.cameras.delete(id);
    for (const group of [this.debugBoxes, this.zones]) {
      for (const child of group.children.filter((c) => c.userData.item === id)) group.remove(child);
    }
    if (!object) return;
    if (!duration || object.isLight) {
      this.root.remove(object);
      return;
    }
    const from = object.matrix.clone().compose(object.position, object.quaternion, object.scale);
    const to = from.clone().multiply(new THREE.Matrix4().makeScale(0.001, 0.001, 0.001));
    this.tween(object, from, to, duration, () => this.root.remove(object));
  }

  /** Blends a material (and its flat or double-sided variants) to new values. */
  fadeMaterial(id, def, duration) {
    const target = this.makeMaterial(def);
    for (const [key, m] of this.materials) {
      if (key !== id && !key.startsWith(id + "|")) continue;
      this.fades.push({
        m,
        c0: m.color.clone(),
        c1: target.color.clone(),
        e0: m.emissive?.clone(),
        e1: target.emissive?.clone(),
        o0: m.opacity,
        o1: target.opacity,
        rest: { roughness: target.roughness, metalness: target.metalness },
        start: performance.now(),
        duration,
      });
    }
  }

  // Marks: coloured outlines and labels the viewer can put on objects -------------------

  /** Outlines items in colour, with an optional label above each. marks: [{node | item, color, label}]. Pass [] to clear. */
  showMarks(marks) {
    this.marks.clear();
    this.clearLabels();
    for (const mark of marks) {
      const items = this.lowered.items.filter((i) => (mark.item ? i.id === mark.item : i.node === mark.node || i.id === mark.node));
      let anchor = null;
      for (const item of items) {
        const size = this.itemSize(item);
        if (!size) continue;
        const box = new THREE.LineSegments(
          new THREE.EdgesGeometry(new THREE.BoxGeometry(size[0] + 0.05, size[1] + 0.05, size[2] + 0.05).translate(0, size[1] / 2, 0)),
          new THREE.LineBasicMaterial({ color: new THREE.Color(mark.color), depthTest: false, transparent: true, opacity: 0.95 }),
        );
        box.renderOrder = 11;
        const live = this.byItem.get(item.id);
        if (live) box.userData.follow = live;
        applyMatrix(box, item.matrix);
        this.marks.add(box);
        anchor ??= { object: live ?? box, height: size[1] };
      }
      if (mark.label && anchor) {
        const el = document.createElement("div");
        el.className = "wv-label";
        el.textContent = mark.label;
        el.style.cssText = `position:absolute;left:0;top:0;transform:translate(-50%,-100%);white-space:nowrap;
          font:500 12px/1.3 system-ui,sans-serif;color:#fff;background:rgba(12,16,20,.82);padding:3px 7px;
          border-radius:4px;border-left:3px solid ${mark.color};max-width:340px;overflow:hidden;text-overflow:ellipsis`;
        this.labelLayer.appendChild(el);
        this.labels.push({ el, ...anchor });
      }
    }
  }

  clearLabels() {
    this.labels = [];
    this.labelLayer.replaceChildren();
  }

  placeLabels() {
    if (!this.labels.length) return;
    const w = this.container.clientWidth, h = this.container.clientHeight;
    const v = new THREE.Vector3();
    const placed = [];
    for (const label of this.labels) {
      label.object.updateMatrixWorld();
      v.set(0, label.height + 0.08, 0).applyMatrix4(label.object.matrixWorld).project(this.camera);
      label.shown = v.z < 1 && Math.abs(v.x) < 1.2 && Math.abs(v.y) < 1.2;
      label.x = ((v.x + 1) / 2) * w;
      label.y = ((1 - v.y) / 2) * h;
    }
    // Stack labels that would cover each other, nearest-to-the-top first.
    for (const label of [...this.labels].sort((a, b) => a.y - b.y)) {
      label.el.style.display = label.shown ? "" : "none";
      if (!label.shown) continue;
      const lw = label.el.offsetWidth, lh = label.el.offsetHeight + 2;
      let y = label.y;
      for (let moved = true; moved; ) {
        moved = false;
        for (const r of placed) {
          if (Math.abs(label.x - r.x) < (lw + r.w) / 2 && y > r.y - lh && y - lh < r.y) {
            y = r.y - r.h;
            moved = true;
          }
        }
      }
      placed.push({ x: label.x, y, w: lw, h: lh });
      label.el.style.translate = `${label.x}px ${y}px`;
    }
  }

  /** Outlines every item that came from these IR nodes. Pass [] to clear. */
  highlight(nodeIds) {
    this.highlights.clear();
    const wanted = new Set(nodeIds);
    for (const item of this.lowered.items) {
      if (!wanted.has(item.node)) continue;
      const size = this.itemSize(item);
      if (!size) continue;
      const box = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(size[0] + 0.04, size[1] + 0.04, size[2] + 0.04).translate(0, size[1] / 2, 0)),
        new THREE.LineBasicMaterial({ color: HIGHLIGHT, depthTest: false }),
      );
      box.renderOrder = 10;
      const live = this.byItem.get(item.id);
      if (live) box.userData.follow = live;
      applyMatrix(box, item.matrix);
      this.highlights.add(box);
    }
  }

  setOption(name, value) {
    this.options[name] = value;
    this.applyOptions();
  }

  applyOptions() {
    this.debugBoxes.visible = this.options.boxes;
    this.zones.visible = this.options.zones;
  }

  useCamera(id) {
    if (id === "orbit" || !this.cameras.has(id)) {
      this.camera = this.orbitCamera;
      this.controls.enabled = true;
      return;
    }
    const c = this.cameras.get(id);
    const aspect = this.container.clientWidth / Math.max(1, this.container.clientHeight);
    const cam =
      c.projection === "orthographic"
        ? new THREE.OrthographicCamera((-c.ortho_height * aspect) / 2, (c.ortho_height * aspect) / 2, c.ortho_height / 2, -c.ortho_height / 2, c.near, c.far)
        : new THREE.PerspectiveCamera(c.fov_deg, aspect, c.near, c.far);
    applyMatrix(cam, c.matrix);
    cam.scale.set(1, 1, 1);
    if (c.look_at) cam.lookAt(...c.look_at);
    this.camera = cam;
    this.controls.enabled = false;
  }

  /** Puts the orbit camera where a scene camera is, looking where it looks, and keeps orbiting enabled. */
  orbitFrom(id) {
    const c = this.cameras.get(id);
    if (!c) return;
    this.useCamera("orbit");
    this.orbitCamera.position.setFromMatrixPosition(new THREE.Matrix4().fromArray(c.matrix));
    if (c.look_at) this.controls.target.set(...c.look_at);
    this.orbitCamera.far = Math.max(this.orbitCamera.far, c.far);
    this.orbitCamera.updateProjectionMatrix();
    this.controls.update();
  }

  frameAll() {
    const b = this.lowered.bounds;
    if (!b) return;
    const min = new THREE.Vector3(...b[0]);
    const max = new THREE.Vector3(...b[1]);
    const center = min.clone().add(max).multiplyScalar(0.5);
    const radius = max.distanceTo(min) / 2;
    const dist = radius / Math.sin(THREE.MathUtils.degToRad(this.orbitCamera.fov / 2)) * 0.9;
    this.orbitCamera.position.copy(center).add(new THREE.Vector3(-0.55, 0.75, -0.65).normalize().multiplyScalar(dist));
    this.orbitCamera.near = Math.max(0.05, dist / 200);
    this.orbitCamera.far = dist * 50;
    this.orbitCamera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.update();
  }

  pick(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.camera);
    const hit = ray.intersectObjects(this.root.children, true).find((h) => h.object.visible && isShown(h.object));
    this.dispatchEvent(new CustomEvent("select", { detail: hit ? { node: hit.object.userData.node, item: hit.object.userData.item } : null }));
  }

  // Frame loop ------------------------------------------------------------------

  frame() {
    if (!this.scene) return;
    const now = performance.now();
    this.tweens = this.tweens.filter((t) => {
      const k = Math.min(1, (now - t.start) / t.duration);
      const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      t.object.position.lerpVectors(t.p0, t.p1, e);
      t.object.quaternion.slerpQuaternions(t.q0, t.q1, e);
      t.object.scale.lerpVectors(t.s0, t.s1, e);
      if (k >= 1 && t.onDone) t.onDone();
      return k < 1;
    });
    this.fades = this.fades.filter((f) => {
      const k = Math.min(1, (now - f.start) / f.duration);
      f.m.color.lerpColors(f.c0, f.c1, k);
      if (f.e0 && f.e1) f.m.emissive.lerpColors(f.e0, f.e1, k);
      f.m.opacity = f.o0 + (f.o1 - f.o0) * k;
      if (k >= 1) Object.assign(f.m, f.rest);
      return k < 1;
    });
    for (const box of [...this.highlights.children, ...this.marks.children]) {
      const live = box.userData.follow;
      if (live) box.position.copy(live.position), box.quaternion.copy(live.quaternion);
    }
    if (this.controls.enabled) this.controls.update();
    if (this.behaviors.length) this.animate(this.time());
    this.cutaway();
    if (this.skyGroup) {
      this.skyGroup.position.copy(this.camera.position);
      this.skyGroup.scale.setScalar(this.camera.far * 0.9);
    }
    if (this.composer) {
      this.renderPass.camera = this.camera;
      this.composer.render();
    } else {
      this.renderer.render(this.scene, this.camera);
    }
    this.placeLabels();
  }

  cutaway() {
    const cam = this.camera.position;
    for (const object of this.root.children) {
      const { role, facing } = object.userData;
      if (role === "ceiling") {
        object.visible = this.options.ceiling || !this.options.cutaway || cam.y < object.position.y - 0.1;
      } else if (facing && this.options.cutaway) {
        const dx = cam.x - object.position.x;
        const dz = cam.z - object.position.z;
        object.visible = dx * facing[0] + dz * facing[1] <= 0;
      } else if (facing) {
        object.visible = true;
      }
    }
  }

  resize() {
    const w = this.container.clientWidth;
    const h = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(w, h);
    this.composer?.setSize(w, h);
    this.orbitCamera.aspect = w / h;
    this.orbitCamera.updateProjectionMatrix();
    if (this.camera.isPerspectiveCamera && this.camera !== this.orbitCamera) {
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
  }

  warn(message) {
    this.warnings.push(message);
    console.warn(`[world-view] ${message}`);
  }
}

/** The lowered scene after a patch, mirroring world_ir.patch.apply_patch. */
export function patchScene(scene, patch) {
  const changed = new Map((patch.changed ?? []).map((i) => [i.id, i]));
  const gone = new Set(patch.removed ?? []);
  return {
    ...scene,
    items: [...scene.items.filter((i) => !gone.has(i.id)).map((i) => changed.get(i.id) ?? i), ...(patch.added ?? [])],
    materials: { ...scene.materials, ...(patch.materials ?? {}) },
    assets: { ...scene.assets, ...(patch.assets ?? {}) },
    bounds: patch.bounds ?? scene.bounds,
    behaviors: patch.behaviors ?? scene.behaviors,
  };
}

/** True when two versions of an item differ only in where they are and whether they show. */
function samePlacementOnly(a, b) {
  const strip = ({ matrix, aabb, visible, look_at, target, ...rest }) => rest;
  return JSON.stringify(strip(a)) === JSON.stringify(strip(b));
}

function toArrayBuffer(data) {
  if (data instanceof ArrayBuffer) return data;
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** A repeatable random sequence from a string, so a world's stars are the same on every load. */
function seededRandom(text) {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => {
    h = (h + 0x6d2b79f5) | 0;
    let t = Math.imul(h ^ (h >>> 15), 1 | h);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Surface texture for a planet or moon: bands for gas giants, spots for rocky bodies. */
function bodyTexture(body, random) {
  const canvas = Object.assign(document.createElement("canvas"), { width: 256, height: 128 });
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = body.color;
  ctx.fillRect(0, 0, 256, 128);
  if (body.style === "gas" || body.style === "ice") {
    ctx.fillStyle = body.band_color ?? "#ffffff";
    for (let y = 0; y < 128; ) {
      const h = 2 + random() * (body.style === "gas" ? 9 : 4);
      ctx.globalAlpha = 0.25 + random() * 0.5;
      ctx.fillRect(0, y, 256, h);
      y += h + 2 + random() * 10;
    }
  } else if (body.style === "rocky") {
    for (let i = 0; i < 60; i++) {
      ctx.globalAlpha = 0.15 + random() * 0.25;
      ctx.fillStyle = random() < 0.5 ? "#000000" : "#ffffff";
      ctx.beginPath();
      ctx.arc(random() * 256, 10 + random() * 108, 2 + random() * 12, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function isShown(object) {
  for (let o = object; o; o = o.parent) if (!o.visible) return false;
  return true;
}

function kelvinColor(kelvin) {
  const t = kelvin / 100;
  const r = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592;
  const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492;
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  const c = (v) => Math.max(0, Math.min(255, v)) / 255;
  return new THREE.Color().setRGB(c(r), c(g), c(b), THREE.SRGBColorSpace);
}
