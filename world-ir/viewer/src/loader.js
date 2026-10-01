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

function shapeGeometry(shape, [w, h, d]) {
  let g;
  switch (shape) {
    case "box":
      g = new THREE.BoxGeometry(w, h, d);
      break;
    case "sphere":
      g = new THREE.SphereGeometry(0.5, 32, 16).scale(w, h, d);
      break;
    case "cylinder":
      g = new THREE.CylinderGeometry(0.5, 0.5, 1, 48).scale(w, h, d);
      break;
    case "cone":
      g = new THREE.ConeGeometry(0.5, 1, 48).scale(w, h, d);
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
  constructor(container, { assetBase = "./", debug = false } = {}) {
    super();
    this.container = container;
    this.assetBase = assetBase;
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
    this.warnings = [];
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
    this.scene.add(this.root, this.debugBoxes, this.zones, this.highlights);
    this.byItem = new Map();
    this.cameras = new Map();
    this.materials = new Map(Object.entries(lowered.materials).map(([id, m]) => [id, this.makeMaterial(m)]));
    this.applyEnvironment(lowered.environment, lowered.bounds);
    await Promise.all(lowered.items.map((item) => this.addItem(item)));
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

  applyEnvironment(env, bounds) {
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

  sunDirection({ elevation_deg, azimuth_deg }) {
    const el = THREE.MathUtils.degToRad(elevation_deg);
    const az = THREE.MathUtils.degToRad(azimuth_deg);
    return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  }

  async addItem(item) {
    let object;
    if (item.type === "asset") object = await this.makeAsset(item);
    else if (item.type === "shape") object = new THREE.Mesh(shapeGeometry(item.shape, item.size), this.material(item.material));
    else if (item.type === "slab") object = new THREE.Mesh(slabGeometry(item.polygon, item.thickness), this.material(item.material));
    else if (item.type === "light") object = this.makeLight(item);
    else if (item.type === "camera") {
      this.cameras.set(item.id, item);
      return;
    } else if (item.type === "zone") {
      this.zones.add(this.makeZone(item));
      return;
    }
    if (!object) return;
    applyMatrix(object, item.matrix);
    object.visible = item.visible !== false;
    object.userData = { node: item.node, item: item.id, role: item.role, facing: item.facing };
    object.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = item.role !== "rug" && item.role !== "window";
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

  material(id) {
    const m = this.materials.get(id);
    if (!m) this.warn(`unknown material '${id}'`);
    return m ?? new THREE.MeshStandardMaterial({ color: 0xff00ff });
  }

  async glb(uri) {
    if (!this.glbCache.has(uri)) this.glbCache.set(uri, this.gltf.loadAsync(new URL(uri, new URL(this.assetBase, location.href)).href));
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

  tween(object, from, to, duration) {
    const p0 = new THREE.Vector3(), q0 = new THREE.Quaternion(), s0 = new THREE.Vector3();
    const p1 = new THREE.Vector3(), q1 = new THREE.Quaternion(), s1 = new THREE.Vector3();
    from.decompose(p0, q0, s0);
    to.decompose(p1, q1, s1);
    this.tweens.push({ object, p0, q0, s0, p1, q1, s1, start: performance.now(), duration });
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
      return k < 1;
    });
    for (const box of this.highlights.children) {
      const live = box.userData.follow;
      if (live) box.position.copy(live.position), box.quaternion.copy(live.quaternion);
    }
    if (this.controls.enabled) this.controls.update();
    this.cutaway();
    this.renderer.render(this.scene, this.camera);
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
