/* Magnific atoms — browser approximation sims (NOT Remotion pixel export) */
(function () {
  "use strict";

  const FPS = 30;
  const DUR = 150;
  const PALETTE = {
    black: "#000000",
    oxblood: "#260306",
    oxbloodSoft: "#2a0a18",
    paper: "#f4f0e8",
    hotPink: "#ff1f96",
    white: "#ffffff",
    ink: "#240a14",
    greyMenu: "#3f3f3f",
  };

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const invLerp = (a, b, v) => (b === a ? 0 : (v - a) / (b - a));
  const map = (v, a, b, c, d, soft) => {
    let t = clamp(invLerp(a, b, v), 0, 1);
    if (soft === "expoIn") t = t * t * t;
    else if (soft === "expoOut") t = 1 - Math.pow(1 - t, 3);
    else if (soft === "smooth") t = t * t * (3 - 2 * t);
    else if (soft === "snappy") t = 1 - Math.pow(1 - t, 2.4);
    return lerp(c, d, t);
  };
  const easePreset = (name) => {
    if (name === "soft") return "expoOut";
    if (name === "snappy") return "snappy";
    if (name === "hero") return "smooth";
    return "smooth";
  };

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function makeTransport(root, state, onScrub) {
    const bar = el("div", "transport");
    const play = el("button", "", "▶ Play");
    const pause = el("button", "", "⏸ Pause");
    const restart = el("button", "", "↺ Restart");
    const scrub = el("input", "scrub");
    scrub.type = "range";
    scrub.min = 0;
    scrub.max = state.duration;
    scrub.step = 1;
    scrub.value = 0;
    const readout = el("span", "frame-readout", "f0 / " + state.duration);
    bar.append(play, pause, restart, scrub, readout);
    root.appendChild(bar);

    play.onclick = () => {
      state.playing = true;
      play.classList.add("active");
      pause.classList.remove("active");
    };
    pause.onclick = () => {
      state.playing = false;
      pause.classList.add("active");
      play.classList.remove("active");
    };
    restart.onclick = () => {
      state.frame = 0;
      scrub.value = 0;
      onScrub(0);
    };
    scrub.oninput = () => {
      state.playing = false;
      play.classList.remove("active");
      state.frame = +scrub.value;
      onScrub(state.frame);
    };
    state._scrub = scrub;
    state._readout = readout;
    state._setPlayUI = (p) => {
      play.classList.toggle("active", p);
      pause.classList.toggle("active", !p);
    };
    return bar;
  }

  function makeKnob(panel, def, onChange) {
    const wrap = el("div", "knob");
    const lab = el("label");
    const name = el("span", "", def.label);
    const val = el("span", "val", "");
    lab.append(name, val);
    wrap.appendChild(lab);
    let input;
    if (def.type === "range") {
      input = document.createElement("input");
      input.type = "range";
      input.min = def.min;
      input.max = def.max;
      input.step = def.step || 1;
      input.value = def.value;
      val.textContent = String(def.value);
      input.oninput = () => {
        val.textContent = input.value;
        onChange(def.key, +input.value);
      };
    } else if (def.type === "color") {
      input = document.createElement("input");
      input.type = "color";
      input.value = def.value;
      val.textContent = def.value;
      input.oninput = () => {
        val.textContent = input.value;
        onChange(def.key, input.value);
      };
    } else if (def.type === "select") {
      input = document.createElement("select");
      def.options.forEach((o) => {
        const opt = document.createElement("option");
        opt.value = o;
        opt.textContent = o;
        if (o === def.value) opt.selected = true;
        input.appendChild(opt);
      });
      val.textContent = def.value;
      input.onchange = () => {
        val.textContent = input.value;
        onChange(def.key, input.value);
      };
    } else {
      input = document.createElement("input");
      input.type = "text";
      input.value = def.value;
      val.textContent = "text";
      input.oninput = () => onChange(def.key, input.value);
    }
    wrap.appendChild(input);
    panel.appendChild(wrap);
    return input;
  }


  /* ---------- Asset slots (session-only Object URLs) ---------- */
  const assetOverrides = new Map(); // slotId -> objectURL

  function resolveAsset(slotId, defaultSrc) {
    return assetOverrides.get(slotId) || defaultSrc;
  }

  function setAssetOverride(slotId, objectURL) {
    const prev = assetOverrides.get(slotId);
    if (prev && String(prev).startsWith("blob:")) {
      try { URL.revokeObjectURL(prev); } catch (_) {}
    }
    if (objectURL) assetOverrides.set(slotId, objectURL);
    else assetOverrides.delete(slotId);
  }

  function makeAssetSlots(panel, defs, onApply) {
    if (!defs || !defs.length) return;
    const section = el("div", "asset-section");
    section.appendChild(el("h4", "", "素材槽 · 上传即换图"));
    section.appendChild(
      el(
        "div",
        "asset-note",
        "Session-only · 接口名 = Magnific 源码常量 / staticFile 路径"
      )
    );
    defs.forEach((def) => {
      const row = el("div", "asset-slot");
      const thumb = el("div", "asset-thumb");
      const img = document.createElement("img");
      img.alt = "";
      img.src = resolveAsset(def.id, def.defaultSrc);
      thumb.appendChild(img);
      if (!def.defaultSrc) {
        thumb.classList.add("empty");
        img.style.display = "none";
        thumb.appendChild(el("span", "asset-empty", "N/A"));
      }

      const meta = el("div", "asset-meta");
      meta.appendChild(el("div", "asset-label", def.label || def.id));
      meta.appendChild(el("code", "asset-iface", def.iface || def.id));

      const actions = el("div", "asset-actions");
      const file = document.createElement("input");
      file.type = "file";
      file.accept = "image/*";
      file.className = "asset-file";
      const uploadBtn = el("label", "asset-btn", "上传");
      uploadBtn.appendChild(file);
      const resetBtn = el("button", "asset-btn ghost", "默认");
      resetBtn.type = "button";

      file.onchange = () => {
        const f = file.files && file.files[0];
        if (!f) return;
        const url = URL.createObjectURL(f);
        setAssetOverride(def.id, url);
        img.style.display = "";
        img.src = url;
        thumb.classList.remove("empty");
        const empty = thumb.querySelector(".asset-empty");
        if (empty) empty.remove();
        onApply(def.id, url);
        file.value = "";
      };
      resetBtn.onclick = () => {
        setAssetOverride(def.id, null);
        if (def.defaultSrc) {
          img.style.display = "";
          img.src = def.defaultSrc;
          thumb.classList.remove("empty");
          const empty = thumb.querySelector(".asset-empty");
          if (empty) empty.remove();
        } else {
          img.style.display = "none";
          img.removeAttribute("src");
          thumb.classList.add("empty");
          if (!thumb.querySelector(".asset-empty")) {
            thumb.appendChild(el("span", "asset-empty", "N/A"));
          }
        }
        onApply(def.id, null);
      };

      actions.append(uploadBtn, resetBtn);
      meta.appendChild(actions);
      row.append(thumb, meta);
      section.appendChild(row);
    });
    if (defs.some((d) => d.note)) {
      const notes = defs.filter((d) => d.note).map((d) => d.note);
      section.appendChild(el("div", "asset-note", notes.join(" · ")));
    }
    panel.appendChild(section);
  }

  function bootAtom(section, build) {
    const stageHost = section.querySelector("[data-stage]");
    const knobHost = section.querySelector("[data-knobs]");
    const transportHost = section.querySelector("[data-transport]");
    if (!stageHost || !knobHost || !transportHost) return;

    const knobs = {};
    const state = { frame: 0, playing: true, duration: DUR, speed: 1 };
    const api = build(stageHost, knobs, state);

    const panel = el("div");
    panel.appendChild(el("h4", "", "Live knobs · 改即重跑"));
    knobHost.innerHTML = "";
    knobHost.appendChild(panel);

    (api.knobDefs || []).forEach((d) => {
      knobs[d.key] = d.value;
      makeKnob(panel, d, (key, v) => {
        knobs[key] = v;
        if (key === "speed") state.speed = v;
        if (key === "duration") {
          state.duration = Math.round(v);
          if (state._scrub) {
            state._scrub.max = state.duration;
          }
        }
        if (api.onKnob) api.onKnob(key, v);
        // restart soft: keep frame but re-render; hard restart on copy/duration
        if (key === "duration" || key.startsWith("t") || key.includes("text") || key.includes("Text") || key.includes("copy") || key === "line1" || key === "line2" || key === "wordmark") {
          state.frame = 0;
        }
        api.render(state.frame, knobs);
        if (state._scrub) state._scrub.value = state.frame;
        if (state._readout) state._readout.textContent = "f" + Math.round(state.frame) + " / " + state.duration;
      });
    });

    function applyAssetChange() {
      if (api.applyAssets) api.applyAssets(knobs);
      state.frame = 0;
      state.playing = true;
      if (state._setPlayUI) state._setPlayUI(true);
      api.render(0, knobs);
      if (state._scrub) state._scrub.value = 0;
      if (state._readout) state._readout.textContent = "f0 / " + state.duration;
    }
    makeAssetSlots(panel, api.assetDefs || [], () => applyAssetChange());
    if (api.applyAssets) api.applyAssets(knobs);

    transportHost.innerHTML = "";
    makeTransport(transportHost, state, (f) => api.render(f, knobs));
    state._setPlayUI(true);

    let last = performance.now();
    function tick(now) {
      const dt = (now - last) / 1000;
      last = now;
      if (state.playing) {
        state.frame += dt * FPS * (state.speed || 1);
        if (state.frame >= state.duration) state.frame = 0;
        if (state._scrub) state._scrub.value = Math.round(state.frame);
        if (state._readout)
          state._readout.textContent =
            "f" + Math.round(state.frame) + " / " + state.duration;
        api.render(state.frame, knobs);
      }
      requestAnimationFrame(tick);
    }
    api.render(0, knobs);
    requestAnimationFrame(tick);
  }

  /* ---------- ATOM 01 Cascade ---------- */
  function buildCascade(stage) {
    stage.innerHTML = "";
    stage.style.background = "#000";
    const colTop = el("div", "layer");
    const colBot = el("div", "layer");
    const ring = el("div", "layer");
    const cam = el("div", "layer");
    cam.style.transformOrigin = "50% 50%";
    const text = el("div", "center-text");
    const period = el("div", "period", ".");
    cam.append(colTop, colBot, ring, text, period);
    stage.appendChild(cam);

    const faceDefs = [
      { id: "a01-aigen1", label: "Cascade 01", iface: "AIGEN(1) · staticFile('aigen/scene01/cascade_01.jpg')", defaultSrc: "stills/s01_cascade_01.jpg" },
      { id: "a01-aigen3", label: "Cascade 03", iface: "AIGEN(3) · staticFile('aigen/scene01/cascade_03.jpg')", defaultSrc: "stills/s01_cascade_03.jpg" },
      { id: "a01-aigen8", label: "Cascade 08", iface: "AIGEN(8) · staticFile('aigen/scene01/cascade_08.jpg')", defaultSrc: "stills/s01_cascade_08.jpg" },
      { id: "a01-top0", label: "TOP_COLUMN[0]", iface: "TOP_COLUMN[0].src = AIGEN(7)", defaultSrc: "stills/s01_cascade_01.jpg" },
      { id: "a01-top2", label: "TOP_COLUMN[2]", iface: "TOP_COLUMN[2].src = AIGEN(1)", defaultSrc: "stills/s01_cascade_03.jpg" },
      { id: "a01-ring0", label: "RING[0]", iface: "RING[0].src = AIGEN(8)", defaultSrc: "stills/s01_cascade_08.jpg" },
    ];
    const facesNow = () => faceDefs.map((d) => resolveAsset(d.id, d.defaultSrc));
    const topCards = [],
      botCards = [],
      ringCards = [];
    for (let i = 0; i < 6; i++) {
      const c = el("div", "card");
      c.style.width = "72px";
      c.style.height = "96px";
      c.innerHTML = `<img src="${facesNow()[i % 6]}" alt="">`;
      colTop.appendChild(c);
      topCards.push(c);
    }
    for (let i = 0; i < 6; i++) {
      const c = el("div", "card");
      c.style.width = "72px";
      c.style.height = "96px";
      c.innerHTML = `<img src="${facesNow()[(i + 2) % 6]}" alt="">`;
      colBot.appendChild(c);
      botCards.push(c);
    }
    const ringLayout = [
      [-280, -160, -18],
      [-180, -220, 8],
      [-40, -240, -6],
      [120, -210, 14],
      [260, -140, -10],
      [300, -20, 6],
      [270, 120, -12],
      [160, 210, 10],
      [20, 240, -8],
      [-140, 210, 12],
      [-280, 130, -14],
      [-320, 10, 4],
      [-200, -60, 16],
      [40, 40, -4],
    ];
    for (let i = 0; i < 14; i++) {
      const c = el("div", "card");
      c.style.width = "64px";
      c.style.height = "86px";
      c.innerHTML = `<img src="${facesNow()[i % 6]}" alt="">`;
      c.style.opacity = 0;
      ring.appendChild(c);
      ringCards.push(c);
    }

    function applyCascadeAssets() {
      const f = facesNow();
      topCards.forEach((c, i) => {
        const im = c.querySelector("img");
        if (im) im.src = f[i % 6];
      });
      botCards.forEach((c, i) => {
        const im = c.querySelector("img");
        if (im) im.src = f[(i + 2) % 6];
      });
      ringCards.forEach((c, i) => {
        const im = c.querySelector("img");
        if (im) im.src = f[i % 6];
      });
    }

    const defaultTimings = [
      { text: "we", inFrame: 4, outFrame: 14 },
      { text: "we are", inFrame: 14, outFrame: 38 },
      { text: "stepping up", inFrame: 40, outFrame: 64 },
      { text: "so you can", inFrame: 66, outFrame: 105 },
      { text: "level up", inFrame: 105, outFrame: 142 },
    ];

    return {
      assetDefs: faceDefs,
      applyAssets: applyCascadeAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "colSpeed", label: "COL_SPEED (px/f)", type: "range", min: 4, max: 28, step: 1, value: 14 },
        { key: "gapHalf", label: "TEXT_GAP_HALF", type: "range", min: 80, max: 280, step: 4, value: 200 },
        { key: "feel", label: "Easing feel", type: "select", options: ["soft", "snappy", "hero"], value: "snappy" },
        { key: "bg", label: "BG", type: "color", value: "#000000" },
        { key: "t0", label: "TYPE[0]", type: "text", value: "we" },
        { key: "t1", label: "TYPE[1]", type: "text", value: "we are" },
        { key: "t2", label: "TYPE[2]", type: "text", value: "stepping up" },
        { key: "t3", label: "TYPE[3]", type: "text", value: "so you can" },
        { key: "t4", label: "TYPE[4]", type: "text", value: "level up" },
        { key: "showCards", label: "Show cards", type: "select", options: ["on", "off"], value: "on" },
      ],
      render(frame, knobs) {
        const W = stage.clientWidth || 640;
        const H = stage.clientHeight || 360;
        const sx = W / 1920,
          sy = H / 1080;
        const cx = W / 2,
          cy = H / 2;
        const gap = (knobs.gapHalf || 200) * sy;
        const colSpeed = knobs.colSpeed || 14;
        const feel = easePreset(knobs.feel);
        stage.style.background = knobs.bg || "#000";

        const explodeT = map(frame, 88, 112, 0, 1, feel);
        const cameraScale = map(frame, 128, 148, 1, 9, "expoIn");
        const cameraOpacity = map(frame, 128, 148, 1, 0);
        const columnsOpacity = map(frame, 105, 112, 1, 0);
        const show = knobs.showCards !== "off";

        colTop.style.opacity = show ? columnsOpacity : 0;
        colBot.style.opacity = show ? columnsOpacity : 0;
        colTop.style.transform = `translate(${lerp(0, -340 * sx, explodeT)}px, ${lerp(0, -160 * sy, explodeT)}px) rotate(${lerp(0, -28, explodeT)}deg)`;
        colBot.style.transform = `translate(${lerp(0, 360 * sx, explodeT)}px, ${lerp(0, 180 * sy, explodeT)}px) rotate(${lerp(0, 32, explodeT)}deg)`;

        const drop = map(frame, 0, 12, H * 0.55, 0);
        const scroll = frame * colSpeed * sy;
        const stride = 110 * sy;
        topCards.forEach((c, i) => {
          const y = cy - gap - 40 * sy - ((i * stride + scroll) % (6 * stride)) - drop;
          c.style.left = cx - 200 * sx + "px";
          c.style.top = y + "px";
          c.style.transform = `rotate(${-6 + (i % 3) * 3}deg)`;
        });
        botCards.forEach((c, i) => {
          const y = cy + gap - 20 * sy + ((i * stride + scroll * 0.95) % (6 * stride)) + drop * 0.3;
          c.style.left = cx + 110 * sx + "px";
          c.style.top = y + "px";
          c.style.transform = `rotate(${4 - (i % 3) * 3}deg)`;
        });

        ringCards.forEach((c, i) => {
          const delay = i * 1.2;
          const t = map(frame - delay, 90, 118, 0, 1, feel);
          const [rx, ry, rot] = ringLayout[i];
          const x = cx + lerp(rx * 0.15, rx, t) * sx;
          const y = cy + lerp(ry * 0.15, ry, t) * sy;
          const sc = lerp(0.4, 1.08, t) * (t > 0.7 ? lerp(1.08, 1, (t - 0.7) / 0.3) : 1);
          c.style.left = x - 32 + "px";
          c.style.top = y - 43 + "px";
          c.style.opacity = show ? clamp(t * 4, 0, 1) * (1 - map(frame, 128, 145, 0, 1)) : 0;
          c.style.transform = `rotate(${rot}deg) scale(${sc})`;
        });

        const texts = [
          knobs.t0 || defaultTimings[0].text,
          knobs.t1 || defaultTimings[1].text,
          knobs.t2 || defaultTimings[2].text,
          knobs.t3 || defaultTimings[3].text,
          knobs.t4 || defaultTimings[4].text,
        ];
        let active = null;
        defaultTimings.forEach((t, i) => {
          if (frame >= t.inFrame && frame < t.outFrame) active = texts[i];
        });
        text.textContent = active || "";
        text.style.opacity = cameraOpacity;
        period.style.opacity = frame >= 142 ? cameraOpacity : 0;

        cam.style.transform = `scale(${cameraScale})`;
        cam.style.opacity = frame >= 128 ? cameraOpacity : 1;
      },
    };
  }

  /* ---------- ATOM 02 Expand ---------- */
  function buildExpand(stage) {
    stage.innerHTML = "";
    const title = el("div", "center-text two");
    const blackText = el("div", "center-text");
    const cardsLayer = el("div", "layer");
    stage.append(cardsLayer, title, blackText);
    const scatterDefs = [
      { id: "a02-c0", label: "Scatter 01", iface: "CARDS[0].src · staticFile('aigen/scene02/scatter_01.png')", defaultSrc: "stills/s02_scatter_01.jpg" },
      { id: "a02-c1", label: "Scatter 02", iface: "CARDS[1].src · staticFile('aigen/scene02/scatter_02.png')", defaultSrc: "stills/s02_scatter_02.jpg" },
      { id: "a02-c2", label: "Scatter 03", iface: "CARDS[2].src · staticFile('aigen/scene02/scatter_03.png')", defaultSrc: "stills/s02_scatter_03.jpg" },
      { id: "a02-c3", label: "Scatter 04", iface: "CARDS[3].src · staticFile('aigen/scene02/scatter_04.png')", defaultSrc: "stills/s02_scatter_01.jpg" },
      { id: "a02-c4", label: "Scatter 05", iface: "CARDS[4].src · staticFile('aigen/scene02/scatter_05.png')", defaultSrc: "stills/s02_scatter_02.jpg" },
      { id: "a02-c5", label: "Scatter 06", iface: "CARDS[5].src · staticFile('aigen/scene02/scatter_06.png')", defaultSrc: "stills/s02_scatter_03.jpg" },
    ];
    const scatterNow = () => scatterDefs.map((d) => resolveAsset(d.id, d.defaultSrc));
    const targets = [
      [-280, -160],
      [260, -180],
      [-40, -220],
      [200, 40],
      [-240, 80],
      [40, 200],
      [-120, 180],
      [280, 160],
    ];
    const cards = targets.map((t, i) => {
      const c = el("div", "card");
      c.style.width = i === 2 ? "90px" : "70px";
      c.style.height = i === 2 ? "120px" : "94px";
      const srcs = scatterNow();
      c.innerHTML = `<img src="${srcs[i % 6]}" alt="">`;
      cardsLayer.appendChild(c);
      return { el: c, tx: t[0], ty: t[1], delay: i * 1.5 };
    });
    function applyExpandAssets() {
      const srcs = scatterNow();
      cards.forEach((c, i) => {
        const im = c.el.querySelector("img");
        if (im) im.src = srcs[i % 6];
      });
    }

    return {
      assetDefs: scatterDefs,
      applyAssets: applyExpandAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "line1", label: "Title L1", type: "text", value: "EXPAND" },
        { key: "line2", label: "Title L2", type: "text", value: "YOUR REACH" },
        { key: "withText", label: "with", type: "text", value: "with" },
        { key: "proText", label: "pro line", type: "text", value: "pro workflows" },
        { key: "andText", label: "and", type: "text", value: "and" },
        { key: "push", label: "PUSH_APART_PX", type: "range", min: 40, max: 220, step: 5, value: 155 },
        { key: "bg", label: "Oxblood BG", type: "color", value: "#2a0808" },
        { key: "feel", label: "Feel", type: "select", options: ["soft", "snappy", "hero"], value: "hero" },
      ],
      render(frame, knobs) {
        const W = stage.clientWidth || 640,
          H = stage.clientHeight || 360;
        const sx = W / 1920,
          sy = H / 1080;
        const cx = W / 2,
          cy = H / 2;
        const feel = easePreset(knobs.feel);
        const wipe = map(frame, 60, 78, 0, 1);
        stage.style.background =
          wipe > 0
            ? `linear-gradient(${lerp(180, 200, wipe)}deg, ${knobs.bg} ${lerp(100, 0, wipe)}%, #000 ${lerp(100, 40, wipe)}%)`
            : knobs.bg;

        const enter = map(frame, 0, 18, 0, 1, feel);
        const push = map(frame, 24, 38, 0, 1, "smooth") * (knobs.push || 155) * sy;
        const hold = frame < 38;
        title.style.opacity = hold ? enter : 0;
        title.innerHTML = `${knobs.line1 || "EXPAND"}\n${knobs.line2 || "YOUR REACH"}`;
        title.style.transform = `translate(-50%, calc(-50% - ${push * 0.15}px)) scale(${lerp(0.86, 1, enter)})`;
        // split push visually via letter-spacing / padding simulation
        title.style.paddingBottom = push + "px";
        title.style.marginTop = -push / 2 + "px";

        cards.forEach((c, i) => {
          const bloom = map(frame - c.delay, 24, 56, 0, 1, feel);
          const drift = map(frame - c.delay, 56, 145, 0, 1);
          const x = cx + c.tx * sx * (bloom + drift * 0.35);
          const y = cy + c.ty * sy * (bloom + drift * 0.35);
          c.el.style.left = x - 35 + "px";
          c.el.style.top = y - 47 + "px";
          c.el.style.opacity = bloom * (1 - map(frame, 70, 95, 0, 1));
          c.el.style.transform = `scale(${lerp(0.2, 1, bloom)}) rotate(${(i - 4) * 6 * bloom}deg)`;
        });

        let bt = "";
        if (frame >= 72 && frame < 96) bt = knobs.withText || "with";
        else if (frame >= 96 && frame < 142) bt = knobs.proText || "pro workflows";
        else if (frame >= 142) bt = knobs.andText || "and";
        blackText.textContent = bt;
        blackText.style.opacity = bt ? 1 : 0;
        blackText.style.color = "#fff";
      },
    };
  }

  /* ---------- ATOM 03 MenuPolygon ---------- */
  function buildMenu(stage) {
    stage.innerHTML = "";
    const menu = el("div", "layer");
    const pointer = el("div", "");
    pointer.style.cssText =
      "position:absolute;color:#ff1f96;font-size:22px;z-index:10;left:18%;top:50%;transform:translateY(-50%);";
    pointer.textContent = "▶";
    const portraitDefs = [
      { id: "a03-assistant", label: "Assistant", iface: "PORTRAIT_FOR['Assistant'].gen · staticFile('aigen/scene03/portrait_assistant.png')", defaultSrc: "stills/s03_assistant.jpg" },
      { id: "a03-lipsync", label: "Lip Sync", iface: "PORTRAIT_FOR['Lip Sync'].gen · staticFile('aigen/scene03/portrait_lipsync.png')", defaultSrc: "stills/s03_voiceClone.jpg" },
      { id: "a03-voice", label: "Voice Cloning", iface: "PORTRAIT_FOR['Voice Cloning'].gen · staticFile('aigen/scene03/portrait_voiceClone.png')", defaultSrc: "stills/s03_voiceClone.jpg" },
      { id: "a03-skin", label: "Skin Enhancer", iface: "PORTRAIT_FOR['Skin Enhancer'].gen · staticFile('aigen/scene03/portrait_skin.png')", defaultSrc: "stills/s03_dancer.jpg" },
      { id: "a03-image", label: "AI Image Generator", iface: "PORTRAIT_FOR['AI Image Generator'].gen · staticFile('aigen/scene03/portrait_image.png')", defaultSrc: "stills/s03_assistant.jpg" },
      { id: "a03-dancer", label: "endless tools", iface: "PORTRAIT_FOR['endless tools'].gen · staticFile('aigen/scene03/portrait_dancer.png')", defaultSrc: "stills/s03_dancer.jpg" },
    ];
    const portraitByLabel = {
      Assistant: "a03-assistant",
      "Lip Sync": "a03-lipsync",
      "Voice Cloning": "a03-voice",
      "Skin Enhancer": "a03-skin",
      "AI Image Generator": "a03-image",
      "endless tools": "a03-dancer",
    };
    const portraitDefault = {
      "a03-assistant": "stills/s03_assistant.jpg",
      "a03-lipsync": "stills/s03_voiceClone.jpg",
      "a03-voice": "stills/s03_voiceClone.jpg",
      "a03-skin": "stills/s03_dancer.jpg",
      "a03-image": "stills/s03_assistant.jpg",
      "a03-dancer": "stills/s03_dancer.jpg",
    };
    function portraitSrcFor(label) {
      const id = portraitByLabel[label] || "a03-assistant";
      return resolveAsset(id, portraitDefault[id]);
    }
    const portrait = el("div", "card");
    portrait.style.cssText = "width:120px;height:160px;right:12%;top:50%;transform:translateY(-50%);opacity:0;z-index:8;";
    portrait.innerHTML = `<img src="${portraitSrcFor("Assistant")}" alt="">`;
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    poly.setAttribute("viewBox", "0 0 1920 1080");
    poly.style.cssText = "position:absolute;inset:0;width:100%;height:100%;z-index:12;";
    const polyPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    polyPath.setAttribute("fill", "none");
    polyPath.setAttribute("stroke", "#ff1f96");
    polyPath.setAttribute("stroke-width", "6");
    poly.appendChild(polyPath);
    const thatText = el("div", "center-text", "that");
    thatText.style.opacity = 0;
    thatText.style.zIndex = 15;
    stage.append(menu, pointer, portrait, poly, thatText);

    const items = [
      "AI Video Upscaler",
      "Lip Sync",
      "Text to Speech",
      "Voice Cloning",
      "Sound Effects",
      "Assistant",
      "and endless tools",
      "Video Editor",
      "endless tools",
      "Skin Enhancer",
      "AI Image Generator",
      "Mockup Generator",
    ];
    const rows = items.map((label) => {
      const r = el("div", "");
      r.textContent = label;
      r.style.cssText =
        "position:absolute;left:28%;width:50%;font-weight:700;font-size:clamp(14px,2.2vw,28px);color:#3f3f3f;white-space:nowrap;";
      menu.appendChild(r);
      return r;
    });
    const snaps = [
      { at: 30, label: "Assistant" },
      { at: 36, label: "Lip Sync" },
      { at: 42, label: "Voice Cloning" },
      { at: 48, label: "Skin Enhancer" },
      { at: 54, label: "AI Image Generator" },
      { at: 60, label: "endless tools" },
    ];

    return {
      assetDefs: portraitDefs,
      applyAssets: function () {
        /* imgs refreshed each render via portraitSrcFor */
      },
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "activeColor", label: "Active text", type: "color", value: "#ffffff" },
        { key: "neon", label: "Polygon stroke", type: "color", value: "#ff1f96" },
        { key: "bgA", label: "BG black", type: "color", value: "#000000" },
        { key: "bgB", label: "BG oxblood", type: "color", value: "#260306" },
        { key: "finalLabel", label: "Final label", type: "text", value: "endless tools" },
        { key: "thatCopy", label: '"that" copy', type: "text", value: "that" },
        { key: "feel", label: "Slot feel", type: "select", options: ["soft", "snappy", "hero"], value: "snappy" },
      ],
      render(frame, knobs) {
        const H = stage.clientHeight || 360;
        const itemH = H * 0.085;
        const bgCut = frame >= 100;
        stage.style.background = bgCut ? knobs.bgB : knobs.bgA;
        polyPath.setAttribute("stroke", knobs.neon || "#ff1f96");
        pointer.style.color = knobs.neon || "#ff1f96";

        // slot index via snaps
        let activeIdx = items.indexOf("and endless tools");
        let portraitImg = portraitSrcFor("Assistant");
        let portraitOn = frame >= 28 && frame < 84;
        for (let i = 0; i < snaps.length; i++) {
          if (frame >= snaps[i].at) {
            activeIdx = items.indexOf(snaps[i].label);
            if (activeIdx < 0) activeIdx = 5 + i;
            portraitImg = portraitSrcFor(snaps[i].label);
          }
        }
        if (frame >= 60) {
          const fi = items.indexOf(knobs.finalLabel || "endless tools");
          if (fi >= 0) activeIdx = fi;
        }
        const vacuum = map(frame, 64, 70, 1, 0, "expoIn");
        const menuOp = frame < 8 ? map(frame, 4, 8, 0, 1) : frame < 64 ? 1 : vacuum;
        const scrollY = activeIdx * itemH;
        rows.forEach((r, i) => {
          const y = H / 2 - scrollY + i * itemH - itemH / 2;
          r.style.top = y + "px";
          const active = i === activeIdx;
          r.style.color = active ? knobs.activeColor : knobs.greyMenu || "#3f3f3f";
          r.style.opacity = menuOp * (active ? 1 : 0.55);
          r.style.transform = `scale(${active ? 1.05 : 0.92})`;
          if (frame >= 98) r.style.opacity = 0;
        });
        pointer.style.opacity = frame >= 18 && frame < 100 ? menuOp : frame >= 92 && frame < 104 ? 1 : 0;
        if (frame >= 92) {
          const spin = map(frame, 92, 100, 0, 1);
          pointer.style.transform = `translateY(-50%) rotate(${spin * 120}deg) scale(${1 + spin * 2})`;
        } else {
          pointer.style.transform = "translateY(-50%)";
        }

        portrait.querySelector("img").src = portraitImg;
        portrait.style.opacity = portraitOn ? map(frame, 28, 34, 0, 1) * (1 - map(frame, 78, 84, 0, 1)) : 0;
        portrait.style.right = frame >= 64 ? "40%" : "12%";

        // polygon triangle → quad
        const draw = map(frame, 104, 116, 0, 1);
        const morph = map(frame, 116, 130, 0, 1, easePreset(knobs.feel));
        const tri = [
          [960, 320],
          [720, 760],
          [1200, 760],
        ];
        const quad = [
          [640, 280],
          [1280, 280],
          [1360, 800],
          [560, 800],
        ];
        let pts;
        if (frame < 104) pts = [];
        else if (morph <= 0) {
          // draw triangle progressively
          pts = tri;
        } else {
          pts = [
            [
              lerp(tri[0][0], quad[0][0], morph),
              lerp(tri[0][1], quad[0][1], morph),
            ],
            [
              lerp(tri[0][0], quad[1][0], morph),
              lerp(tri[0][1], quad[1][1], morph),
            ],
            [
              lerp(tri[2][0], quad[2][0], morph),
              lerp(tri[2][1], quad[2][1], morph),
            ],
            [
              lerp(tri[1][0], quad[3][0], morph),
              lerp(tri[1][1], quad[3][1], morph),
            ],
          ];
        }
        if (pts.length) {
          const d =
            "M " +
            pts
              .map((p) => p.join(" "))
              .join(" L ") +
            " Z";
          polyPath.setAttribute("d", d);
          polyPath.style.strokeDasharray = "4000";
          polyPath.style.strokeDashoffset = String(4000 * (1 - draw));
          poly.style.opacity = 1;
        } else {
          poly.style.opacity = 0;
        }
        thatText.textContent = knobs.thatCopy || "that";
        thatText.style.opacity = map(frame, 118, 126, 0, 1) * (1 - map(frame, 138, 148, 0, 1));
      },
    };
  }

  /* ---------- ATOM 04 RewriteDream ---------- */
  function buildRewrite(stage) {
    stage.innerHTML = "";
    const boardDefs = [
      { id: "a04-clouds", label: "Pink clouds board", iface: "staticFile('aigen/scene04/clouds_pink.png') (+ .mp4 in Scene)", defaultSrc: "stills/s04_clouds_pink.jpg" },
      { id: "a04-falling", label: "Falling figure", iface: "staticFile('aigen/scene04/falling_figure_alpha.png')", defaultSrc: "stills/s04_falling.png" },
      { id: "a04-mountain", label: "Mountain peak", iface: "staticFile('aigen/scene04/mountain_peak.png') (+ .mp4)", defaultSrc: "stills/s04_mountain.jpg" },
      { id: "a04-walking", label: "Walking figure", iface: "staticFile('aigen/scene04/walking_figure_alpha.png')", defaultSrc: "stills/s04_walking.png" },
    ];
    const plate = el("div", "layer");
    const mountain = el("div", "layer");
    mountain.style.opacity = 0;
    const mask = el("div", "layer");
    mask.style.cssText =
      "background:#260306;clip-path:polygon(50% 45%, 42% 58%, 58% 58%);";
    const fig = el("img");
    fig.style.cssText =
      "position:absolute;left:50%;top:50%;width:18%;transform:translate(-50%,-40%);opacity:0;z-index:6;";
    const walk = el("img");
    walk.style.cssText =
      "position:absolute;left:50%;top:55%;width:22%;transform:translate(-50%,-50%);opacity:0;z-index:6;";
    function applyRewriteAssets() {
      const clouds = resolveAsset("a04-clouds", "stills/s04_clouds_pink.jpg");
      const mt = resolveAsset("a04-mountain", "stills/s04_mountain.jpg");
      plate.style.background = "center/cover no-repeat url(" + JSON.stringify(clouds) + ")";
      mountain.style.background = "center/cover no-repeat url(" + JSON.stringify(mt) + ")";
      fig.src = resolveAsset("a04-falling", "stills/s04_falling.png");
      walk.src = resolveAsset("a04-walking", "stills/s04_walking.png");
    }
    applyRewriteAssets();
    const copy = el("div", "center-text two");
    copy.style.zIndex = 10;
    stage.append(plate, mountain, mask, fig, walk, copy);
    stage.style.background = "#260306";

    return {
      assetDefs: boardDefs,
      applyAssets: applyRewriteAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "rewrite1", label: "REWRITE L1", type: "text", value: "REWRITE" },
        { key: "rewrite2", label: "REWRITE L2", type: "text", value: "THE RULES" },
        { key: "dream1", label: "DREAM L1", type: "text", value: "DREAM" },
        { key: "dream2", label: "DREAM L2", type: "text", value: "BIGGER" },
        { key: "swapAt", label: "Dream swap frame", type: "range", min: 90, max: 120, step: 1, value: 108 },
        { key: "feel", label: "Feel", type: "select", options: ["soft", "snappy", "hero"], value: "soft" },
        { key: "showFigure", label: "Figures", type: "select", options: ["on", "off"], value: "on" },
      ],
      render(frame, knobs) {
        const feel = easePreset(knobs.feel);
        const windowOpen = map(frame, 0, 28, 0, 1, feel);
        // expand clip from tiny triangle to full
        const open = lerp(8, 55, windowOpen);
        mask.style.clipPath = `polygon(50% ${45 - open * 0.3}%, ${50 - open}% ${55 + open * 0.4}%, ${50 + open}% ${55 + open * 0.4}%, 50% ${45 - open * 0.3}%)`;
        // better: use inset circle growing
        const r = lerp(4, 80, windowOpen);
        mask.style.clipPath = `circle(${r}% at 50% 50%)`;
        mask.style.opacity = 0; // mask reveals plate underneath by covering oxblood — invert approach
        // Actually: oxblood full, plate visible through growing circle
        stage.style.background = "#260306";
        plate.style.clipPath = `circle(${r}% at 50% 50%)`;
        plate.style.transform = `scale(${lerp(1.04, 1.12, map(frame, 0, 70, 0, 1))})`;

        const exitPunch = map(frame, 95, 109, 0, 1, "expoIn");
        plate.style.filter = `blur(${exitPunch * 18}px)`;
        plate.style.opacity = 1 - map(frame, 105, 118, 0, 1);

        const swap = knobs.swapAt || 108;
        const inDream = frame >= swap;
        mountain.style.opacity = inDream ? map(frame, swap, swap + 10, 0, 1) : 0;
        mountain.style.clipPath = "none";

        if (!inDream) {
          copy.innerHTML = `${knobs.rewrite1 || "REWRITE"}\n${knobs.rewrite2 || "THE RULES"}`;
          copy.style.opacity = map(frame, 10, 24, 0, 1) * (1 - exitPunch);
          const push = map(frame, 10, 60, 0, 1) * 12;
          copy.style.transform = `translate(-50%, calc(-50% - ${push}px))`;
        } else {
          copy.innerHTML = `${knobs.dream1 || "DREAM"}\n${knobs.dream2 || "BIGGER"}`;
          copy.style.opacity = map(frame, swap, swap + 8, 0, 1);
          copy.style.transform = "translate(-50%, -50%) scale(1)";
        }
        const showF = knobs.showFigure !== "off";
        fig.style.opacity = showF && !inDream ? map(frame, 20, 32, 0, 1) * (1 - exitPunch) : 0;
        walk.style.opacity = showF && inDream ? map(frame, swap + 4, swap + 14, 0, 1) : 0;
      },
    };
  }

  /* ---------- ATOM 05 MuchToday ---------- */
  function buildMuch(stage) {
    stage.innerHTML = "";
    const muchDefs = [
      { id: "a05-clouds", label: "Clouds / BG", iface: "staticFile('aigen/scene05/bg_clouds_01.png')", defaultSrc: "stills/s05_clouds.jpg" },
      { id: "a05-leap", label: "Leap figure (sim still)", iface: "staticFile('aigen/scene05/figure_leap.mp4') · sim 用静帧替代", defaultSrc: "", note: "真 Scene 是 MP4；此处可上传静帧近似" },
    ];
    const clouds = el("div", "layer");
    function applyMuchAssets() {
      const board = resolveAsset("a05-clouds", "stills/s05_clouds.jpg");
      const leap = assetOverrides.get("a05-leap") || "";
      const bg = leap || board;
      clouds.style.background = "center/cover no-repeat url(" + JSON.stringify(bg) + ")";
    }
    applyMuchAssets();
    const copy = el("div", "center-text two");
    copy.style.zIndex = 5;
    const beams = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    beams.setAttribute("viewBox", "0 0 1920 1080");
    beams.style.cssText = "position:absolute;inset:0;width:100%;height:100%;z-index:4;";
    const topBeam = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const botBeam = document.createElementNS("http://www.w3.org/2000/svg", "path");
    [topBeam, botBeam].forEach((p) => {
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", "#ff1f96");
      p.setAttribute("stroke-width", "5");
      p.setAttribute("stroke-linecap", "round");
      beams.appendChild(p);
    });
    stage.append(clouds, beams, copy);

    return {
      assetDefs: muchDefs,
      applyAssets: applyMuchAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "much1", label: "MUCH", type: "text", value: "MUCH" },
        { key: "much2", label: "BIGGER", type: "text", value: "BIGGER" },
        { key: "today", label: "today", type: "text", value: "today" },
        { key: "brand", label: "brand lockup", type: "text", value: "Freepik" },
        { key: "jumpEnd", label: "PHASE_JUMP_END", type: "range", min: 40, max: 90, step: 1, value: 72 },
        { key: "neon", label: "Beam color", type: "color", value: "#ff1f96" },
        { key: "feel", label: "Feel", type: "select", options: ["soft", "snappy", "hero"], value: "snappy" },
      ],
      render(frame, knobs) {
        const jumpEnd = knobs.jumpEnd || 72;
        const zoomEnd = jumpEnd + 18;
        const todayEnd = zoomEnd + 36;
        const zoomLocal = frame - jumpEnd;
        const inZoom = zoomLocal > 0 && frame < zoomEnd + 4;
        const zoomScale = inZoom ? map(zoomLocal, 0, 18, 1, 22, "expoIn") : 1;
        const zoomBlur = inZoom ? map(zoomLocal, 0, 18, 0, 28) : 0;
        clouds.style.transform = `scale(${frame < jumpEnd ? lerp(1, 1.08, frame / jumpEnd) : zoomScale})`;
        clouds.style.filter = `blur(${zoomBlur}px)`;
        clouds.style.opacity = inZoom ? map(zoomLocal, 4, 18, 1, 0) : frame < zoomEnd ? 1 : 0;

        topBeam.setAttribute("stroke", knobs.neon);
        botBeam.setAttribute("stroke", knobs.neon);

        if (frame < jumpEnd) {
          stage.style.background = "#87a0b8";
          copy.innerHTML = `${knobs.much1 || "MUCH"}\n${knobs.much2 || "BIGGER"}`;
          copy.style.opacity = map(frame, 4, 16, 0, 1, easePreset(knobs.feel));
          copy.style.color = "#fff";
          beams.style.opacity = 0;
        } else if (frame < zoomEnd) {
          copy.style.opacity = map(zoomLocal, 0, 8, 1, 0);
          beams.style.opacity = 0;
        } else {
          stage.style.background = "#260306";
          clouds.style.opacity = 0;
          const local = frame - zoomEnd;
          const inFreepik = frame >= todayEnd;
          if (!inFreepik) {
            copy.textContent = knobs.today || "today";
            copy.className = "center-text";
            copy.style.opacity = map(local, 0, 4, 0, 1);
            copy.style.transform = `translate(-50%,-50%) scale(${map(local, 0, 14, 1.55, 1, "expoOut")})`;
          } else {
            copy.textContent = knobs.brand || "Freepik";
            copy.className = "center-text";
            const fl = frame - todayEnd;
            copy.style.opacity = 1;
            copy.style.transform = `translate(-50%,-50%) scale(${map(fl, 0, 14, 1.32, 1, "expoOut")})`;
          }
          // beams
          let beamScale = 0;
          if (!inFreepik) beamScale = map(local, 8, 36, 0, 0.72);
          else beamScale = map(frame - todayEnd, 0, 10, 0.72, 1, "snappy");
          beams.style.opacity = beamScale > 0 ? 1 : 0;
          const spread = 200 + beamScale * 700;
          topBeam.setAttribute(
            "d",
            `M ${960 - spread} 0 L 960 400 L ${960 + spread} 0`
          );
          botBeam.setAttribute(
            "d",
            `M ${960 - spread} 1080 L 960 680 L ${960 + spread} 1080`
          );
        }
      },
    };
  }

  /* ---------- ATOM 06 Transforms ---------- */
  function buildTransforms(stage) {
    stage.innerHTML = "";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 1920 1080");
    svg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;";
    const top = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const bot = document.createElementNS("http://www.w3.org/2000/svg", "path");
    [top, bot].forEach((p) => {
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", "#ff1f96");
      p.setAttribute("stroke-width", "8");
      p.setAttribute("stroke-linecap", "round");
      p.style.filter = "drop-shadow(0 0 12px #ff1f96)";
      svg.appendChild(p);
    });
    const copy = el("div", "center-text");
    const flash = el("div", "layer");
    flash.style.background = "#fff";
    flash.style.opacity = 0;
    const mWrap = el("div", "");
    mWrap.style.cssText =
      "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:8;opacity:0;";
    const mSvgHtml = `<svg width="120" height="120" viewBox="0 0 100 100"><path d="M 0 100 L 10 0 L 34 0 L 50 50 L 66 0 L 90 0 L 100 100 L 62 100 L 50 92 L 38 100 Z" fill="#240a14"/></svg>`;
    mWrap.innerHTML = mSvgHtml;
    const tfDefs = [
      { id: "a06-mono", label: "M monogram (optional)", iface: "MMonogram SVG · 可换品牌图 staticFile('…')", defaultSrc: "", note: "默认纯矢量；上传后用图片替代 MMonogram" },
    ];
    function applyTfAssets() {
      const custom = assetOverrides.get("a06-mono");
      if (custom) {
        mWrap.innerHTML = `<img src="${custom}" alt="" style="width:120px;height:120px;object-fit:contain;display:block">`;
      } else {
        mWrap.innerHTML = mSvgHtml;
      }
    }
    stage.append(svg, copy, flash, mWrap);
    stage.style.background = "#000";

    return {
      assetDefs: tfDefs,
      applyAssets: applyTfAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "wordA", label: "transforms", type: "text", value: "transforms" },
        { key: "wordB", label: "into", type: "text", value: "into" },
        { key: "cutAt", label: "Hard-cut frame", type: "range", min: 10, max: 40, step: 1, value: 21 },
        { key: "flashAt", label: "FLASH_PEAK", type: "range", min: 40, max: 80, step: 1, value: 58 },
        { key: "neon", label: "Neon", type: "color", value: "#ff1f96" },
        { key: "paper", label: "Paper BG", type: "color", value: "#f4f0e8" },
        { key: "ink", label: "M ink", type: "color", value: "#240a14" },
      ],
      render(frame, knobs) {
        const cut = knobs.cutAt || 21;
        const peak = knobs.flashAt || 58;
        top.setAttribute("stroke", knobs.neon);
        bot.setAttribute("stroke", knobs.neon);
        const topY = map(frame, 0, peak, 290, 30);
        const botY = map(frame, 0, peak, 790, 1050);
        const spread = map(frame, 0, peak, 330, 1300, "expoIn");
        top.setAttribute("d", `M ${960 - spread} 0 L 960 ${topY} L ${960 + spread} 0`);
        bot.setAttribute("d", `M ${960 - spread} 1080 L 960 ${botY} L ${960 + spread} 1080`);

        const showWhite = frame >= peak;
        stage.style.background = showWhite ? knobs.paper : "#000";
        svg.style.opacity = showWhite ? 0 : 1;
        copy.textContent = frame < cut ? knobs.wordA || "transforms" : knobs.wordB || "into";
        copy.style.opacity = showWhite ? 0 : 1;
        copy.style.color = "#fff";

        const flashOp =
          frame >= peak - 4 && frame <= peak + 2
            ? map(frame, peak - 4, peak, 0, 1) * (1 - map(frame, peak, peak + 2, 0, 1))
            : 0;
        flash.style.opacity = flashOp;

        const mIn = peak + 2;
        if (frame >= mIn) {
          const local = frame - mIn;
          const sc = map(local, 0, 12, 6.5, 1, "expoOut");
          mWrap.style.opacity = 1;
          mWrap.style.transform = `translate(-50%,-50%) scale(${sc})`;
          const mp = mWrap.querySelector("path");
          if (mp) mp.setAttribute("fill", knobs.ink || "#240a14");
        } else {
          mWrap.style.opacity = 0;
        }
      },
    };
  }

  /* ---------- ATOM 07 Lockup ---------- */
  function buildLockup(stage) {
    stage.innerHTML = "";
    stage.style.background = PALETTE.paper;
    const row = el("div", "");
    row.style.cssText =
      "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;align-items:center;gap:14px;";
    const logoHost = el("div", "");
    logoHost.style.cssText = "width:56px;height:56px;flex-shrink:0;display:flex;align-items:center;justify-content:center;";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("width", "56");
    svg.setAttribute("height", "56");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute(
      "d",
      "M 0 100 L 10 0 L 34 0 L 50 50 L 66 0 L 90 0 L 100 100 L 62 100 L 50 92 L 38 100 Z"
    );
    path.setAttribute("fill", PALETTE.ink);
    svg.appendChild(path);
    logoHost.appendChild(svg);
    const word = el("div", "");
    word.style.cssText =
      "overflow:hidden;white-space:nowrap;font-weight:700;font-size:clamp(22px,4vw,42px);letter-spacing:-0.04em;color:#240a14;line-height:1;";
    const inner = el("div", "", "Magnific");
    word.appendChild(inner);
    row.append(logoHost, word);
    stage.appendChild(row);
    const lockDefs = [
      { id: "a07-logo", label: "Logo / monogram (optional)", iface: "M path (Scene07) · 可换品牌 logo staticFile('…')", defaultSrc: "", note: "默认 SVG M；上传后替换落幅 logo" },
    ];
    function applyLockAssets() {
      const custom = assetOverrides.get("a07-logo");
      logoHost.innerHTML = "";
      if (custom) {
        const im = document.createElement("img");
        im.src = custom;
        im.alt = "";
        im.style.cssText = "width:56px;height:56px;object-fit:contain;display:block";
        logoHost.appendChild(im);
      } else {
        logoHost.appendChild(svg);
      }
    }

    return {
      assetDefs: lockDefs,
      applyAssets: applyLockAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "wordmark", label: "Wordmark", type: "text", value: "Magnific" },
        { key: "paper", label: "Paper", type: "color", value: "#f4f0e8" },
        { key: "ink", label: "Ink", type: "color", value: "#240a14" },
        { key: "revealStart", label: "Reveal start f", type: "range", min: 0, max: 20, step: 1, value: 4 },
        { key: "revealEnd", label: "Reveal end f", type: "range", min: 12, max: 60, step: 1, value: 24 },
        { key: "feel", label: "Feel", type: "select", options: ["soft", "snappy", "hero"], value: "soft" },
      ],
      render(frame, knobs) {
        stage.style.background = knobs.paper || PALETTE.paper;
        if (path.isConnected) path.setAttribute("fill", knobs.ink || PALETTE.ink);
        inner.style.color = knobs.ink || PALETTE.ink;
        inner.textContent = knobs.wordmark || "Magnific";
        const a = knobs.revealStart || 4;
        const b = knobs.revealEnd || 24;
        const p = map(frame, a, b, 0, 1, "expoOut");
        // measure roughly
        const full = Math.max(80, (knobs.wordmark || "Magnific").length * 18);
        word.style.width = full * p + "px";
        const fade = 1 - map(frame, 145, 150, 0, 1);
        row.style.opacity = fade;
      },
    };
  }

  /* ---------- ATOM 08 Caption / type ---------- */
  function buildCaption(stage) {
    stage.innerHTML = "";
    stage.style.background = "#111";
    const bgLayer = el("div", "layer");
    bgLayer.style.opacity = 0.35;
    bgLayer.style.zIndex = 1;
    const hard = el("div", "center-text");
    hard.style.zIndex = 10;
    const hero = el("div", "center-text two");
    hero.style.top = "62%";
    hero.style.zIndex = 10;
    const track = el("div", "");
    track.style.cssText =
      "position:absolute;left:8%;right:8%;bottom:10%;height:8px;background:#333;border-radius:99px;overflow:hidden;z-index:5;";
    const marks = el("div", "layer");
    stage.append(bgLayer, hard, hero, track, marks);
    const capDefs = [
      { id: "a08-bg", label: "Optional BG still", iface: "字效轨本身无位图 · 可选底板（非 Magnific 常量）", defaultSrc: "", note: "默认纯文字；上传底板仅影响本预览" },
    ];
    function applyCapAssets() {
      const custom = assetOverrides.get("a08-bg");
      if (custom) {
        bgLayer.style.background = "center/cover no-repeat url(" + JSON.stringify(custom) + ")";
        bgLayer.style.opacity = 0.4;
      } else {
        bgLayer.style.background = "none";
        bgLayer.style.opacity = 0;
      }
    }

    const base = [
      { text: "we", inFrame: 4, outFrame: 14 },
      { text: "we are", inFrame: 14, outFrame: 38 },
      { text: "stepping up", inFrame: 40, outFrame: 64 },
      { text: "so you can", inFrame: 66, outFrame: 105 },
      { text: "level up", inFrame: 105, outFrame: 142 },
    ];

    return {
      assetDefs: capDefs,
      applyAssets: applyCapAssets,
      knobDefs: [
        { key: "speed", label: "Speed", type: "range", min: 0.25, max: 2.5, step: 0.05, value: 1 },
        { key: "mode", label: "Mode", type: "select", options: ["hard-cut TYPE_TIMINGS", "heroEnter two-line"], value: "hard-cut TYPE_TIMINGS" },
        { key: "t0", label: "seg0", type: "text", value: "we" },
        { key: "t1", label: "seg1", type: "text", value: "we are" },
        { key: "t2", label: "seg2", type: "text", value: "stepping up" },
        { key: "t3", label: "seg3", type: "text", value: "so you can" },
        { key: "t4", label: "seg4", type: "text", value: "level up" },
        { key: "hero1", label: "Hero L1", type: "text", value: "EXPAND" },
        { key: "hero2", label: "Hero L2", type: "text", value: "YOUR REACH" },
        { key: "feel", label: "heroEnter feel", type: "select", options: ["soft", "snappy", "hero"], value: "hero" },
        { key: "color", label: "Type color", type: "color", value: "#ffffff" },
      ],
      render(frame, knobs) {
        const texts = [knobs.t0, knobs.t1, knobs.t2, knobs.t3, knobs.t4];
        marks.innerHTML = "";
        base.forEach((t, i) => {
          const m = el("div", "");
          const left = (t.inFrame / DUR) * 100;
          const w = ((t.outFrame - t.inFrame) / DUR) * 100;
          m.style.cssText = `position:absolute;left:${left}%;width:${w}%;bottom:10%;height:8px;background:${i % 2 ? "#f0a98a" : "#b9a7d9"};border-radius:99px;opacity:.85;z-index:6;`;
          marks.appendChild(m);
          const lab = el("div", "");
          lab.textContent = texts[i] || t.text;
          lab.style.cssText = `position:absolute;left:${left}%;bottom:20%;font-size:10px;color:#aaa;transform:translateX(-0%);white-space:nowrap;z-index:6;`;
          marks.appendChild(lab);
        });
        const playhead = el("div", "");
        playhead.style.cssText = `position:absolute;left:${(frame / DUR) * 100}%;bottom:8%;width:2px;height:12px;background:#fff;z-index:7;`;
        marks.appendChild(playhead);

        if ((knobs.mode || "").includes("hero")) {
          hard.style.opacity = 0;
          const p = map(frame, 4, 28, 0, 1, easePreset(knobs.feel));
          const breath = Math.sin((frame - 18) * 0.045) * 0.012;
          hero.innerHTML = `${knobs.hero1 || "EXPAND"}\n${knobs.hero2 || "YOUR REACH"}`;
          hero.style.opacity = p;
          hero.style.color = knobs.color;
          hero.style.transform = `translate(-50%,-50%) translateY(${(1 - p) * 28}px) scale(${p * (1 + breath)})`;
        } else {
          hero.style.opacity = 0;
          let active = "";
          base.forEach((t, i) => {
            if (frame >= t.inFrame && frame < t.outFrame) active = texts[i] || t.text;
          });
          hard.textContent = active;
          hard.style.opacity = active ? 1 : 0;
          hard.style.color = knobs.color;
          // hard cut = no fade
          hard.style.transform = "translate(-50%,-50%)";
        }
      },
    };
  }

  const BUILDERS = {
    a01: buildCascade,
    a02: buildExpand,
    a03: buildMenu,
    a04: buildRewrite,
    a05: buildMuch,
    a06: buildTransforms,
    a07: buildLockup,
    a08: buildCaption,
  };

  function initTabs(root) {
    root.querySelectorAll("[data-tabs]").forEach((tabbar) => {
      const section = tabbar.closest(".atom");
      tabbar.querySelectorAll(".tab").forEach((btn) => {
        btn.addEventListener("click", () => {
          tabbar.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          const id = btn.getAttribute("data-tab");
          section.querySelectorAll("[data-panel]").forEach((p) => {
            p.classList.toggle("active", p.getAttribute("data-panel") === id);
          });
        });
      });
    });
  }

  function checkStills() {
    const img = new Image();
    img.onerror = () => {
      const n = document.querySelector("[data-stills-warn]");
      if (n) n.hidden = false;
    };
    img.src = "stills/s01_cascade_01.jpg";
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTabs(document);
    checkStills();
    Object.keys(BUILDERS).forEach((id) => {
      const section = document.getElementById(id);
      if (section) bootAtom(section, BUILDERS[id]);
    });
    // toc active
    const links = [...document.querySelectorAll(".toc a[href^='#']")];
    const onScroll = () => {
      let cur = links[0];
      links.forEach((a) => {
        const t = document.querySelector(a.getAttribute("href"));
        if (t && t.getBoundingClientRect().top < 120) cur = a;
      });
      links.forEach((a) => a.classList.toggle("active", a === cur));
    };
    document.addEventListener("scroll", onScroll, { passive: true });
  });
})();
