# scene/ 原创视觉资产登记

本目录所有图形均为项目自有的原创 SVG/CSS 绘制（源代码即资产），不引入任何
外部图片、图标库、纹理或第三方 SVG bundle。登记每个视觉元素的创作方式与
参数来源，便于后续维护与合规审计（见 `docs/compliance/content-policy.md`）。

| 文件 | 资产 | 来源与真值约束 |
| --- | --- | --- |
| `LabEnvironment.tsx` | 墙面渐变、双窗（含窗台/窗棂/光斑）、瓷砖缝、安全菱形贴纸、金属层架（搁板+弱立柱）、实验台（背沿/工作面高光条/前立面/投影渐变） | 原创几何；比例由实验自身 slot band 派生，非按实验硬编码；纯装饰，`pointer-events:none` + `aria-hidden` |
| `LabEquipment.tsx` | 12 kind 器材分层绘制：玻璃外壳/内腔裁剪/刻度/前壁高光/口沿双线/壁厚双描边；试剂瓶瓶盖+标签纸；量筒底座；集气筒领口+水封线；滴管胶头锥管；移液管刻度管；温度计液柱；pH 探针读数屏；玻璃棒折射高光；导管 U 形管；热板陶瓷面/旋钮/指示灯/热羽流 | 原创几何；footprint 比例来自 pack `equipment_defs`；刻度来自 `graduations_uL`；读数仅显示 RenderFrame 提供的值，无读数不伪造 |
| `LabPhenomena.tsx` | 液体多边形（贴真实内腔轮廓+弯月面+液面反光线）、浑浊罩层、沉淀床/悬浮颗粒、确定性气泡、蒸汽羽流、热底盘光 | 全部数值来自 `ChemLabRenderFrame`（fill/color token/opacity/turbidity/precipitate/bubbles/steam/温度带）；粒子布局用 `hash(sessionId, objectId, revision, index)` 确定性散列；液位显示有可读性地板（真实但极低的液量画最小可见液池，数值真值仍由标签/读数承载），空容器不画液体 |
| `lab-scene.css` | 场景 token（明暗两套：玻璃沿/内胆/高光/钢材/台面/阴影/液体默认色）、相机过渡、节点滑行、一次性命令动作关键帧（settle/lift/tilt/dip/glow/ripple/flash/pending 脉冲）、气泡/蒸汽/热斑/高亮循环、`prefers-reduced-motion` 全量关停 | 颜色派生自项目 paper/ink design tokens 与场景局部变量；动画时长为展示层设计值，不是引擎公式 |

未知 kind 的兜底轮廓（虚线框 + "?" + 可读名称）同样原创，保证缺失画法时
可见降级而不是静默消失。所有 SVG 引用 ID 以 `useId()` 实例前缀去冲突。
