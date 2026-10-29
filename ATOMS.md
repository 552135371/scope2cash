# Magnific × Remotion · 动效原子库（Live Sim）

> 路径：`/workspace/wiggo/video/remotion-playbook/atoms/`  
> **打开方式（重要）**：必须从本目录起本地服务，否则 `stills/` 相对路径会挂。
>
> ```bash
> cd /workspace/wiggo/video/remotion-playbook/atoms
> python3 -m http.server 8766
> # 浏览器 → http://127.0.0.1:8766/
> ```
>
> 不要从上级目录起服，也不要直接用 `file://` 双击（部分浏览器拦本地图）。  
> 源仓：`/workspace/magnific-remotion`  
> 旧流程卡（怎么开片）：[`../index.html`](../index.html)

## 产品形态

每个原子三栏：

1. **Live 预览** — CSS/Canvas/SVG 近似模拟；右侧 **Live knobs** + **素材槽**（上传图立刻重跑）；Play / Pause / Restart + scrub  
2. **源码** — 真实文件路径 + 摘录  
3. **可调接口** — 硬编码常量对照表（「当前可改」vs「应抽 props」）

页眉：**素材可本地替换；导出真片时把同名接口换进 Remotion Scene。**  
标签：浏览器近似模拟 · 非 Remotion 像素导出。

## 素材槽（v3）

Session-only（`Map<slotId, objectURL>`，无后端）。每个槽：缩略图 · 上传 · 恢复默认 · **Remotion 接口名**。

| 原子 | 槽数 | 接口命名（对齐 Magnific 源码） |
|------|------|--------------------------------|
| 01 Cascade | 6 | `AIGEN(1/3/8)` · `TOP_COLUMN[0/2].src` · `RING[0].src` → `staticFile('aigen/scene01/cascade_XX.jpg')` |
| 02 Expand | 6 | `CARDS[0..5].src` → `staticFile('aigen/scene02/scatter_0N.png')` |
| 03 MenuPolygon | 6 | `PORTRAIT_FOR['Assistant'|…|'endless tools'].gen` → `aigen/scene03/portrait_*.png` |
| 04 RewriteDream | 4 | `clouds_pink` / `falling_figure_alpha` / `mountain_peak` / `walking_figure_alpha` |
| 05 MuchToday | 2 | `bg_clouds_01.png` · `figure_leap.mp4`（sim 用静帧） |
| 06 Transforms | 1 optional | 默认 SVG `MMonogram`；可上传品牌图 |
| 07 Lockup | 1 optional | 默认 SVG M；可上传 logo |
| 08 字幕/字效 | 1 optional | 默认纯文字；可选底板（非源码常量） |

## 原子清单（8 · 均有 Live Sim）

| # | 原子 | 源文件 | 时长 | 模拟要点 |
|---|------|--------|------|----------|
| 01 | Cascade | `Scene01_Cascade.tsx` | 150f | 双列滚卡 + TYPE_TIMINGS 硬切 + 炸环 + 相机 push |
| 02 | Expand | `Scene02_Expand.tsx` | 150f | 标题推开 + 散射 + 黑幕硬切字 |
| 03 | MenuPolygon | `Scene03_MenuPolygon.tsx` | 150f | 槽机停格 + 肖像 + ▶→三角→四边形 |
| 04 | RewriteDream | `Scene04_RewriteDream.tsx` | 150f | 圆形窗口揭示 + 砸镜切 Dream |
| 05 | MuchToday | `Scene05_MuchToday.tsx` | 150f | 云跃缩放 + today/品牌 + V 光束 |
| 06 | Transforms | `Scene06_Transforms.tsx` | 150f | 霓虹 V spread + 闪白 + M 砸落（SVG） |
| 07 | Lockup | `Scene07_MagnificLockup.tsx` | 150f | M + wordmark 宽裁剪揭示 |
| 08 | 字幕/字效 | `helpers.ts` + TYPE_TIMINGS | — | 硬切轨 / heroEnter 双行（优先产品化） |

## 文件

| 文件 | 作用 |
|------|------|
| `index.html` | 原子库页面（预览｜源码｜接口） |
| `sim.js` | 8 原子模拟 + knobs + 素材槽 |
| `sim.css` | 舞台 / 旋钮 / transport / 素材槽 |
| `stills/` | 默认纹理（从 `public/aigen/` 缩略） |
| `ATOMS.md` | 本说明 |

## 抽走复用

1. 在 Live 预览里上传自己的产品图，确认构图。  
2. 记下槽位上的 **接口名**（如 `CARDS[0].src`）。  
3. 回 Magnific Scene 把同名常量 / `staticFile(...)` 换成你的素材路径。  
4. Studio 导出真片。
