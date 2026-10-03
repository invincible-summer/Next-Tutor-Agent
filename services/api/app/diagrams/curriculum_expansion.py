"""Original curriculum scenes; only implemented controls enter the catalogue."""
from __future__ import annotations

import math

from .drawing import Drawing
from .schema import DiagramError


INVENTORY = {
    "physics": """
venturi|文丘里管与测压管|Venturi and piezometers|伯努利,流速,流体
capillary_rise|水的毛细上升对照|Capillary rise|表面张力,浸润
linear_expansion|线膨胀长度对照|Linear thermal expansion|热胀冷缩
bimetal_strip|双金属片弯曲|Bimetal strip|温控,热膨胀
eddy_brake|涡流制动示意|Eddy-current brake|电磁感应,制动
hysteresis_loop|磁滞回线|Magnetic hysteresis|铁磁,磁化
photoelectric_setup|光电效应实验装置|Photoelectric setup|光子,逸出功
rutherford_scattering|卢瑟福散射示意|Rutherford scattering|原子核,金箔
closed_pipe_modes|闭端气柱位移模态|Closed-pipe displacement modes|驻波,声学
half_life_curve|半衰期归一化曲线|Half-life decay curve|放射性,核物理
""",
    "chemistry": """
hydration_shell|阳离子水合壳示意|Cation hydration shell|水合,极性,溶解
ionic_dissolution|离子晶体溶解示意|Ionic dissolution|晶格,离子,溶液
collision_orientation|有效碰撞取向对照|Collision orientation|活化,碰撞理论
hess_paths|赫斯定律双路径|Hess-law pathways|反应焓,热化学
salt_bridge_transport|锌铜电池盐桥迁移|Salt-bridge transport|原电池,电中性
chromatography_retention|色谱保留位置示意|Chromatographic retention|色谱,展开,比移值
soap_micelle|皂胶束截面|Soap micelle section|表面活性剂,去污
buffer_pair|缓冲液共轭酸碱对|Buffer conjugate pair|酸碱,缓冲
""",
    "geography": """
artesian_aquifer|承压含水层与自流井|Artesian aquifer|地下水,水头,自流井
inversion_layer|近地逆温层示意|Temperature inversion|气温,污染扩散
coastal_upwelling|沿岸上升流剖面|Coastal upwelling|洋流,离岸输运
longshore_drift|沿岸漂沙路径|Longshore drift|海岸,侵蚀,沉积
oxbow_cutoff|牛轭湖形成序列|Oxbow cutoff|河曲,裁弯取直
coriolis_comparison|南北半球偏转对照|Coriolis deflection|地转偏向力
latitude_daylight|纬度与昼长示意|Latitude and daylight|太阳赤纬,昼夜
hadley_section|哈德莱环流剖面|Hadley circulation|大气环流,气压带
""",
    "biology": """
phospholipid_vesicle|磷脂囊泡双层截面|Phospholipid vesicle|膜,磷脂,亲水疏水
competitive_inhibition|竞争性抑制结合示意|Competitive inhibition|酶,活性位点
semiconservative_daughters|半保留复制子代双链|Semiconservative daughters|DNA,复制
action_potential_stages|动作电位阶段示意|Action potential stages|神经,膜电位
""",
    "astronomy": """
transit_lightcurve|凌星与光变曲线|Transit light curve|系外行星,凌星
redshift_lines|谱线红移对照|Redshift lines|光谱,波长
""",
    "mathematics": """
unit_circle_projection|单位圆正弦投影|Unit-circle projection|三角函数,正弦
secant_limit|割线逼近切线|Secant limit|导数,切线,变化率
""",
    "statistics": """
conditional_probability_tree|条件概率两层树|Conditional probability tree|概率,分支
regression_outlier|离群点对回归线影响|Regression outlier influence|回归,离群点
""",
    "engineering": """
wheatstone_bridge|惠斯通电桥整体电路|Wheatstone bridge|电桥,测量
rc_lowpass_response|RC低通频率响应|RC low-pass response|滤波,频率响应
""",
    "environment": """
membrane_separation|膜分离三股流|Membrane separation|进料,透过,浓缩
""",
    "agriculture": """
hydroponic_loop|水培营养液循环|Hydroponic loop|水培,营养液,循环
""",
}


def entries():
    rows = []
    for subject, inventory in INVENTORY.items():
        for line in inventory.strip().splitlines():
            variant, title, english, aliases = line.split("|")
            family = subject + "_extended"
            rows.append({"id": family + "." + variant, "renderer": family,
                "variant": variant, "category": subject, "title": title,
                "english": english, "aliases": aliases.split(","), "version": 1,
                "features": [], "sample_params": {}, "license": "original-project-artwork"})
    return rows


def number(default, low, high, *, integer=False, unit=""):
    return {"type": "integer" if integer else "number", "default": default,
        "minimum": low, "maximum": high, "unit": unit}


def parameters(v):
    if v == "closed_pipe_modes":
        return {"mode": number(1, 1, 3, integer=True)}
    if v == "half_life_curve":
        return {"halflives": number(4, 1, 5, integer=True)}
    if v == "chromatography_retention":
        return {"retention_a": number(.35, 0, 1), "retention_b": number(.7, 0, 1)}
    if v == "latitude_daylight":
        return {"latitude": number(40, -60, 60, unit="°"),
            "declination": number(23.44, -23.44, 23.44, unit="°")}
    if v == "redshift_lines":
        return {"shift": {**number(35, 0, 60, unit="diagram_px"), "non_quantitative_allowed": True}}
    if v == "unit_circle_projection":
        return {"angle": number(40, 0, 360, unit="°")}
    if v == "secant_limit":
        return {"step": number(1.3, .2, 2)}
    if v == "conditional_probability_tree":
        return {"p_a": number(.5, .01, 1), "p_b_given_a": number(.6, 0, 1)}
    return {}


def axes(d, x_label, y_label, *, label_x=430, label_y=298):
    d.arrow(55, 265, 450, 265)
    d.arrow(55, 265, 55, 35)
    d.text(x_label, label_x, label_y, size=15)
    d.text(y_label, 55, 23, size=15)


def dot(d, x, y, label, color=None, r=17):
    d.circle(x, y, r, fill=color or d.surface, width=1.5)
    if label:
        d.text(label, x, y+5, size=14)


def draw(v, p, mono=False):
    d = Drawing(480, 320, mono)
    if v == "venturi":
        d.poly([(30, 115), (135, 115), (210, 150), (280, 150), (360, 115), (450, 115)])
        d.poly([(30, 225), (135, 225), (210, 190), (280, 190), (360, 225), (450, 225)])
        for x, bottom, level in [(100, 115, 65), (245, 150, 105)]:
            d.rect(x-6, 35, 12, bottom-35, fill=d.glass, color=d.blue, width=1)
            d.rect(x-5, level, 10, bottom-level, fill=d.blue, color=d.blue, width=1)
        d.arrow(50, 170, 120, 170, color=d.blue)
        d.arrow(215, 170, 275, 170, color=d.blue)
        d.text("A", 100, 251)
        d.text("B", 245, 217)
        d.facts["piezometer_order"] = "A_above_B"
    elif v == "capillary_rise":
        d.rect(40, 190, 400, 100, fill=d.glass, color=d.blue)
        d.line(40, 190, 440, 190, color=d.blue)
        for x, w, level in [(145, 16, 85), (325, 34, 145)]:
            d.rect(x-w/2, 45, w, 230, fill=None)
            d.path(f"M {x-w/2+2} {level} Q {x} {level+12} {x+w/2-2} {level} L {x+w/2-2} 275 L {x-w/2+2} 275 Z", fill=d.blue, color=d.blue, width=1)
        d.text("水 / glass", 240, 309, size=14)
    elif v == "linear_expansion":
        d.rect(50, 65, 14, 180, fill=d.surface)
        d.hatch(50, 65, 14, 180)
        d.rect(64, 90, 280, 24, fill=d.glass, color=d.blue)
        d.rect(64, 200, 330, 24, fill=d.surface, color=d.red)
        d.line(344, 65, 344, 250, dashed=True, color=d.muted, width=1)
        d.text("冷", 425, 110)
        d.text("热", 425, 220)
    elif v == "bimetal_strip":
        d.rect(80, 60, 310, 14, fill=d.blue, color=d.blue)
        d.rect(80, 74, 310, 14, fill=d.gold, color=d.gold)
        d.path("M 80 185 Q 245 166 400 253 L 393 266 Q 236 179 80 199 Z", fill=d.blue, color=d.blue)
        d.path("M 80 199 Q 236 179 393 266 L 385 277 Q 228 192 80 213 Z", fill=d.gold, color=d.gold)
        d.rect(64, 167, 16, 64, fill=d.surface)
        d.text("冷", 440, 83)
        d.text("热", 440, 237)
    elif v == "eddy_brake":
        d.rect(80, 125, 305, 75, fill=d.glass)
        d.rect(195, 126, 100, 73, fill=d.surface, color=d.muted, width=1)
        for x in [214, 245, 276]:
            for y in [139, 185]:
                d.line(x-3, y-3, x+3, y+3, color=d.muted, width=1)
                d.line(x+3, y-3, x-3, y+3, color=d.muted, width=1)
        d.text("B：×进纸面", 245, 71, size=14)
        d.arrow(90, 108, 170, 108, color=d.green)
        d.text("v", 130, 92)
        d.arrow(165, 218, 100, 218, color=d.red)
        d.text("F", 130, 249)
        for x, clockwise in [(195, True), (295, False)]:
            d.circle(x, 164, 18, color=d.gold)
            d.arrow(x-7 if clockwise else x+7, 147, x+7 if clockwise else x-7, 147, color=d.gold, width=1.5)
    elif v == "hysteresis_loop":
        d.arrow(45, 160, 440, 160)
        d.arrow(240, 285, 240, 30)
        pts = [(240+162*math.cos(t), 160-100*math.tanh(2*math.cos(t)+.85*math.sin(t))) for t in [i*math.pi/100 for i in range(201)]]
        d.poly(pts, color=d.blue, width=2.5)
        d.text("H", 450, 166)
        d.text("B", 259, 33)
    elif v == "photoelectric_setup":
        d.ellipse(245, 126, 155, 85, fill=d.glass)
        d.line(135, 80, 135, 175, color=d.blue, width=5)
        d.line(355, 80, 355, 175, color=d.gold, width=5)
        for y in [85, 110, 135]:
            d.arrow(30, y-25, 125, y, color=d.red)
        for x in [178, 229, 282]:
            d.circle(x, 126, 4, fill=d.blue, color=d.blue)
        d.arrow(170, 151, 316, 151, color=d.blue)
        d.poly([(135, 175), (135, 270), (200, 270)])
        d.line(200, 254, 200, 286)
        d.line(214, 245, 214, 295)
        d.poly([(214, 270), (320, 270), (355, 270), (355, 175)])
        dot(d, 295, 270, "A", r=18)
        d.text("−", 184, 240)
        d.text("+", 229, 240)
    elif v == "rutherford_scattering":
        d.rect(32, 140, 38, 36, fill=d.surface)
        d.text("α", 51, 165)
        d.rect(217, 50, 5, 225, fill=d.gold, color=d.gold)
        d.arrow(80, 158, 440, 158, color=d.blue)
        d.poly([(80, 166), (219, 166), (400, 241)], color=d.blue)
        d.arrow(380, 232, 410, 245, color=d.blue)
        d.poly([(80, 148), (219, 148), (142, 74)], color=d.red)
        d.arrow(166, 97, 142, 74, color=d.red)
        d.path("M 368 39 Q 490 160 368 283", color=d.muted, width=3)
        d.text("金箔", 219, 303, size=15)
    elif v == "closed_pipe_modes":
        mode = p["mode"]
        odd = 2*mode-1
        d.poly([(430, 95), (60, 95), (60, 225), (430, 225)])
        d.line(60, 95, 60, 225, width=5)
        d.line(60, 160, 430, 160, color=d.muted, width=1, dashed=True)
        # Displacement is zero at the closed end and extremal at the open end.
        pts = [(60+370*i/150, 160-54*math.sin(odd*math.pi*i/300)) for i in range(151)]
        d.poly(pts, color=d.blue)
        d.poly([(x, 320-y) for x, y in pts], color=d.muted)
        d.text("闭端", 60, 265, size=15)
        d.text("开端", 430, 265, size=15)
        d.text("位移包络", 245, 45, size=15)
        d.facts.update(mode=mode, harmonic=odd)
    elif v == "half_life_curve":
        n = p["halflives"]
        axes(d, "t / T½", "N / N₀", label_x=255, label_y=312)
        d.poly([(55+370*i/100, 265-210*2**(-n*i/100)) for i in range(101)], color=d.blue)
        for i in range(n+1):
            x = 55+370*i/n
            d.line(x, 265, x, 270, width=1)
            d.text(i, x, 286, size=12)
        d.text("1", 40, 60, size=13)
        d.text("0", 40, 270, size=13)
        d.facts["halflives"] = n
    elif v == "hydration_shell":
        dot(d, 240, 160, "Na⁺", d.gold, r=26)
        for i in range(6):
            a = i*math.pi/3
            ox, oy = 240+78*math.cos(a), 160+78*math.sin(a)
            for side in [-1, 1]:
                b = a+side*math.radians(52.25)
                hx, hy = ox+30*math.cos(b), oy+30*math.sin(b)
                d.line(ox, oy, hx, hy, width=1.5)
                dot(d, hx, hy, "H", r=9)
            dot(d, ox, oy, "O", d.red, r=13)
        d.text("水合层截面示意", 240, 300, size=14)
        d.facts["water_orientation"] = "oxygen_towards_cation"
    elif v == "ionic_dissolution":
        for row in range(4):
            for col in range(4):
                plus = (row+col) % 2 == 0
                dot(d, 70+col*32, 90+row*42, "+" if plus else "−", d.gold if plus else d.green, r=13)
        d.arrow(205, 160, 265, 160, color=d.blue)
        d.rect(295, 50, 155, 220, fill=d.glass, color=d.blue)
        for x, y, plus in [(325, 90, True), (414, 85, False), (366, 138, True), (325, 195, False), (417, 220, True), (388, 264-16, False)]:
            dot(d, x, y, "+" if plus else "−", d.gold if plus else d.green, r=13)
        d.text("晶格", 125, 290, size=15)
        d.text("水溶液", 370, 300, size=15)
    elif v == "collision_orientation":
        for y, facing in [(100, True), (225, False)]:
            dot(d, 155, y, "", d.glass, r=31)
            dot(d, 320, y, "", d.glass, r=31)
            d.circle(181 if facing else 130, y, 8, fill=d.red, color=d.red)
            d.circle(294 if facing else 346, y, 8, fill=d.red, color=d.red)
            d.arrow(203, y, 230, y, color=d.blue)
            d.arrow(273, y, 245, y, color=d.blue)
        d.text("取向 A", 76, 105, size=15)
        d.text("取向 B", 76, 230, size=15)
        d.text("红点：反应位点", 240, 302, size=14)
    elif v == "hess_paths":
        for x, y, label in [(90, 85, "A"), (390, 85, "B"), (240, 240, "C")]:
            dot(d, x, y, label, d.glass, r=27)
        d.arrow(122, 85, 358, 85, color=d.blue)
        d.arrow(114, 110, 215, 215, color=d.gold)
        d.arrow(265, 215, 365, 110, color=d.gold)
        d.text("ΔH", 240, 60, size=17)
        d.text("ΔH₁", 136, 182, size=17)
        d.text("ΔH₂", 344, 182, size=17)
    elif v == "salt_bridge_transport":
        for x, label, color in [(60, "Zn", d.muted), (315, "Cu", d.gold)]:
            d.rect(x, 135, 110, 150, fill=d.glass, color=d.blue)
            d.rect(x+28, 109, 16, 145, fill=color)
            d.text(label, x+35, 102, size=16)
        d.poly([(96, 109), (96, 40), (350, 40), (350, 109)])
        d.rect(206, 30, 58, 20, fill=d.surface)
        d.arrow(150, 21, 195, 21, color=d.blue)
        d.text("e⁻", 170, 15, size=13)
        d.path("M 145 217 L 145 120 Q 145 90 174 90 L 291 90 Q 320 90 320 120 L 320 217", color=d.gold, width=8)
        d.arrow(208, 117, 164, 117, color=d.green)
        d.arrow(262, 117, 306, 117, color=d.red)
        d.text("阴离子", 208, 151, size=14)
        d.text("阳离子", 280, 151, size=14)
        d.text("盐桥", 235, 81, size=15)
    elif v == "chromatography_retention":
        d.rect(120, 30, 240, 255, fill=d.surface, width=1.5)
        d.line(130, 60, 350, 60, dashed=True, color=d.blue, width=1.5)
        d.line(130, 260, 350, 260, color=d.muted, width=1.5)
        for x, key, label, color in [(190, "retention_a", "A", d.red), (290, "retention_b", "B", d.blue)]:
            y = 260-200*p[key]
            d.ellipse(x, y, 14, 7, fill=color, color=color)
            d.text(label, x, 305, size=15)
        d.text("溶剂前沿", 368, 65, size=13, anchor="start")
        d.text("起点", 368, 265, size=13, anchor="start")
        d.facts.update(retention_a=p["retention_a"], retention_b=p["retention_b"])
    elif v == "soap_micelle":
        d.circle(240, 158, 56, fill=d.gold, color=d.gold)
        for i in range(20):
            a = i*math.pi/10
            d.poly([(240+r*math.cos(a)+3*math.sin(j*math.pi/2), 158+r*math.sin(a)) for j, r in enumerate([96, 88, 78, 68])], color=d.muted, width=1.5)
            d.circle(240+106*math.cos(a), 158+106*math.sin(a), 7, fill=d.blue, color=d.blue)
        d.text("油", 240, 163)
        d.text("水", 420, 163)
        d.line(370, 70, 331, 104, color=d.muted, width=1)
        d.text("亲水端", 405, 62, size=14)
        d.line(80, 233, 163, 204, color=d.muted, width=1)
        d.text("疏水尾", 73, 257, size=14)
    elif v == "buffer_pair":
        dot(d, 118, 155, "HA", d.glass, r=43)
        dot(d, 305, 155, "A⁻", d.gold, r=43)
        dot(d, 412, 155, "H⁺", r=27)
        d.arrow(173, 145, 247, 145, color=d.blue)
        d.arrow(247, 165, 173, 165, color=d.blue)
        d.text("+", 365, 162)
        d.text("共轭酸碱对", 240, 261, size=16)
    elif v == "artesian_aquifer":
        d.poly([(25, 90), (100, 135), (230, 190), (455, 200), (455, 282), (25, 282)], closed=True, fill=d.surface)
        d.path("M 25 122 Q 170 225 455 222 L 455 251 Q 160 253 25 157 Z", fill=d.glass, color=d.blue)
        d.path("M 25 157 Q 160 253 455 251 L 455 282 L 25 282 Z", fill=d.gold, color=d.gold)
        d.line(25, 120, 455, 120, color=d.blue, dashed=True, width=1)
        d.rect(341, 151, 9, 87, fill=d.glass, color=d.blue)
        d.arrow(345, 229, 345, 132, color=d.blue)
        d.path("M 345 151 Q 345 114 319 147 M 345 151 Q 350 114 373 145", color=d.blue)
        d.arrow(45, 118, 87, 151, color=d.blue)
        d.text("补给区", 78, 58, size=15)
        d.text("水头", 425, 110, size=14)
        d.text("隔水层", 195, 150, size=14)
        d.text("含水层", 245, 238, size=14)
        d.text("自流井", 381, 177, size=14)
    elif v == "inversion_layer":
        axes(d, "T", "高度")
        d.rect(58, 145, 350, 90, fill=d.glass, color=d.glass, width=1)
        d.poly([(110, 265), (150, 235), (315, 145), (230, 55)], color=d.red, width=3)
        d.line(58, 235, 408, 235, dashed=True, color=d.muted, width=1)
        d.line(58, 145, 408, 145, dashed=True, color=d.muted, width=1)
        d.text("逆温层", 360, 202, size=15)
        d.text("地面", 99, 298, size=14)
    elif v == "coastal_upwelling":
        d.rect(25, 115, 430, 165, fill=d.glass, color=d.blue)
        d.poly([(325, 115), (455, 70), (455, 280), (405, 280)], closed=True, fill=d.gold)
        d.arrow(300, 131, 95, 131, color=d.blue, width=3)
        d.path("M 100 257 Q 305 268 345 154", color=d.blue, width=3)
        d.arrow(337, 177, 345, 154, color=d.blue, width=3)
        d.text("离岸表层流", 184, 94, size=15)
        d.text("深层补偿流", 187, 233, size=15)
        d.text("陆地", 419, 137, size=15)
    elif v == "longshore_drift":
        d.rect(25, 35, 430, 110, fill=d.glass, color=d.glass)
        d.rect(25, 145, 430, 130, fill=d.gold, color=d.gold)
        d.line(25, 145, 455, 145, width=2)
        for x in [60, 155, 250, 345]:
            d.arrow(x, 60, x+60, 145, color=d.blue)
            d.arrow(x+60, 150, x+60, 103, color=d.muted)
        d.arrow(85, 218, 387, 218, color=d.ink)
        d.text("斜向入射", 111, 23, size=14)
        d.text("沿岸输运", 240, 253, size=15)
    elif v == "oxbow_cutoff":
        for index, x in enumerate([15, 175, 335]):
            d.rect(x, 40, 140, 240, fill=d.surface, color=d.muted, radius=4, width=1)
            if index < 2:
                d.path(f"M {x+10} 240 L {x+53} 185 C {x-6} 115 {x+26} 64 {x+70} 65 C {x+116} 63 {x+143} 120 {x+88} 185 L {x+132} 240", color=d.blue, width=6)
            if index == 1:
                d.line(x+50, 189, x+91, 189, color=d.blue, width=6)
            if index == 2:
                d.poly([(x+10, 240), (x+50, 200), (x+90, 200), (x+132, 240)], color=d.blue, width=6)
                d.path(f"M {x+45} 158 C {x-6} 108 {x+29} 64 {x+70} 65 C {x+114} 64 {x+145} 111 {x+99} 158", color=d.blue, width=6)
            d.text(index+1, x+70, 306, size=15)
    elif v == "coriolis_comparison":
        for x, sign, label in [(120, 1, "北半球"), (360, -1, "南半球")]:
            d.line(x, 255, x, 60, dashed=True, color=d.muted, width=1)
            d.path(f"M {x} 255 Q {x} 135 {x+sign*65} 70", color=d.blue, width=3)
            d.arrow(x+sign*50, 88, x+sign*65, 70, color=d.blue)
            d.text(label, x, 305, size=16)
        d.text("北", 240, 34, size=16)
    elif v == "latitude_daylight":
        latitude, declination = p["latitude"], p["declination"]
        sunset = math.acos(-math.tan(math.radians(latitude))*math.tan(math.radians(declination)))
        d.circle(240, 157, 95, fill=d.muted)
        points = [(240, 157)] + [(240+95*math.cos(-sunset+2*sunset*i/100), 157+95*math.sin(-sunset+2*sunset*i/100)) for i in range(101)]
        d.poly(points, closed=True, fill=d.glass, color=d.blue, width=1)
        d.text("日", 291, 162)
        d.text("夜", 185, 162)
        d.text(f"φ={latitude:g}°   δ={declination:g}°", 240, 290, size=15)
        d.text("纬线平面示意", 240, 28, size=15)
        d.facts.update(latitude=latitude, declination=declination, daylight_hours=24*sunset/math.pi)
    elif v == "hadley_section":
        d.line(35, 259, 445, 259, width=3)
        d.arrow(240, 235, 240, 72, color=d.red, width=3)
        for x in [85, 395]:
            d.arrow(240, 64, x, 64, color=d.blue)
            d.arrow(x, 72, x, 235, color=d.blue)
            d.arrow(x, 245, 230 if x < 240 else 250, 245, color=d.green)
        d.text("30°N", 85, 291, size=15)
        d.text("0°", 240, 291, size=15)
        d.text("30°S", 395, 291, size=15)
        d.text("对流层环流剖面", 240, 32, size=15)
    elif v == "phospholipid_vesicle":
        d.circle(240, 160, 78, fill=d.glass, color=d.glass)
        for i in range(30):
            a = i*math.pi/15
            for r, direction in [(108, -1), (78, 1)]:
                x, y = 240+r*math.cos(a), 160+r*math.sin(a)
                d.circle(x, y, 5, fill=d.blue, color=d.blue, width=1)
                for shift in [-2, 2]:
                    dx, dy = shift*math.sin(a), -shift*math.cos(a)
                    d.line(x+dx+direction*6*math.cos(a), y+dy+direction*6*math.sin(a), x+dx+direction*13*math.cos(a), y+dy+direction*13*math.sin(a), color=d.gold, width=1)
        d.text("水", 240, 165)
        d.text("水", 409, 165)
        d.text("磷脂双层截面", 240, 304, size=15)
    elif v == "competitive_inhibition":
        for x, occupied in [(55, False), (285, True)]:
            d.path(f"M {x} 195 C {x-10} 121 {x+30} 101 {x+65} 109 L {x+89} 153 L {x+113} 109 C {x+162} 101 {x+180} 180 {x+148} 232 Q {x+46} 267 {x} 195 Z", fill=d.glass, color=d.blue)
            if occupied:
                d.poly([(x+65, 109), (x+89, 153), (x+113, 109)], closed=True, fill=d.red, color=d.red)
            else:
                d.poly([(x+65, 42), (x+89, 86), (x+113, 42)], closed=True, fill=d.gold, color=d.gold)
                d.arrow(x+89, 89, x+89, 105, color=d.muted)
        d.text("底物", 144, 30, size=15)
        d.text("抑制剂占据位点", 374, 80, size=14)
        d.text("酶", 140, 289, size=15)
        d.text("酶", 370, 289, size=15)
    elif v == "semiconservative_daughters":
        def strands(x, y, new=False):
            d.line(x, y, x+120, y, color=d.blue, width=3)
            d.line(x, y+22, x+120, y+22, color=d.gold if new else d.blue, width=3, dashed=new)
            for dx in range(10, 119, 14):
                d.line(x+dx, y+3, x+dx, y+19, color=d.muted, width=1)
        strands(30, 145)
        strands(320, 73, True)
        strands(320, 223, True)
        d.arrow(163, 155, 298, 86, color=d.muted)
        d.arrow(163, 155, 298, 234, color=d.muted)
        d.line(50, 295, 90, 295, color=d.blue, width=3)
        d.text("旧链", 117, 300, size=14)
        d.line(230, 295, 270, 295, color=d.gold, width=3, dashed=True)
        d.text("新链", 304, 300, size=14)
    elif v == "action_potential_stages":
        axes(d, "t", "膜电位")
        d.line(58, 210, 433, 210, color=d.muted, dashed=True, width=1)
        d.path("M 58 210 L 140 210 C 166 210 176 67 206 65 C 235 66 247 257 297 247 Q 329 242 345 210 L 432 210", color=d.blue, width=3)
        d.text("去极化", 120, 125, size=14)
        d.text("复极化", 282, 123, size=14)
        d.text("超极化", 334, 288, size=14)
    elif v == "transit_lightcurve":
        d.line(95, 85, 385, 85, color=d.muted, dashed=True, width=1)
        d.circle(240, 85, 48, fill=d.gold, color=d.gold)
        d.circle(250, 85, 10, fill=d.ink)
        d.arrow(292, 85, 330, 85, color=d.muted, width=1)
        d.arrow(55, 272, 440, 272)
        d.arrow(55, 272, 55, 159)
        d.poly([(60, 190), (157, 190), (195, 230), (287, 230), (324, 190), (428, 190)], color=d.blue, width=3)
        d.text("相对亮度", 61, 151, size=14)
        d.text("t", 443, 295, size=15)
    elif v == "redshift_lines":
        for y, offset, label in [(92, 0, "参考"), (194, p["shift"], "观测")]:
            d.rect(60, y-30, 360, 60, fill=d.surface, color=d.muted, width=1)
            for x in [95, 139, 218, 289]:
                d.line(x+offset, y-25, x+offset, y+25, color=d.blue, width=3)
            d.text(label, 30, y+5, size=13)
        d.arrow(70, 267, 410, 267, color=d.muted)
        d.text("波长 λ", 240, 300, size=15)
        d.facts["shift"] = p["shift"]
    elif v == "unit_circle_projection":
        angle = math.radians(p["angle"])
        x, y = 240+99*math.cos(angle), 159-99*math.sin(angle)
        d.arrow(45, 159, 440, 159)
        d.arrow(240, 292, 240, 26)
        d.circle(240, 159, 99, color=d.blue)
        d.line(240, 159, x, y, color=d.blue)
        d.line(x, y, x, 159, color=d.gold, dashed=True)
        d.line(x, y, 240, y, color=d.green, dashed=True)
        d.circle(x, y, 4, fill=d.blue)
        d.text("P", x+12, y-9, size=14)
        d.text("x", 444, 179, size=15)
        d.text("y", 263, 28, size=15)
        d.text("1", 343, 180, size=12)
        d.facts["angle"] = p["angle"]
    elif v == "secant_limit":
        step = p["step"]
        axes(d, "x", "y")
        to_xy = lambda x, y: (95+94*x, 265-23*y)
        d.poly([to_xy(i*3.7/100, (i*3.7/100)**2) for i in range(101) if (i*3.7/100)**2 <= 9.4], color=d.blue)
        a, b = to_xy(1, 1), to_xy(1+step, (1+step)**2)
        slope = 2+step
        d.poly([to_xy(x, 1+slope*(x-1)) for x in [.5, min(3.5, 1+8/slope)]], color=d.gold)
        d.poly([to_xy(x, 1+2*(x-1)) for x in [.5, 3.5]], color=d.muted)
        for point, label in [(a, "A"), (b, "B")]:
            d.circle(*point, 4, fill=d.ink)
            d.text(label, point[0]+12, point[1]+18, size=14)
        d.facts["step"] = step
    elif v == "conditional_probability_tree":
        pa, pb = p["p_a"], p["p_b_given_a"]
        for x, y, label in [(45, 160, "S"), (230, 83, "A"), (230, 237, "Ā"), (430, 45, "B"), (430, 123, "B̄")]:
            dot(d, x, y, label, d.glass, r=20)
        for a, b in [((67, 153), (207, 91)), ((67, 168), (207, 228)), ((252, 78), (407, 50)), ((252, 89), (407, 119))]:
            d.arrow(*a, *b, color=d.blue)
        for x, y, value in [(139, 109, pa), (139, 216, 1-pa), (333, 43, pb), (333, 120, 1-pb)]:
            d.text(f"{value:g}", x, y, size=14)
        d.text("条件分支：A", 242, 305, size=14)
        d.facts.update(p_a=pa, p_b_given_a=pb)
    elif v == "regression_outlier":
        points = [(1, 1.1), (2, 2.2), (3, 2.9), (4, 4.3), (5, 4.7), (6, 5.8)]
        outlier = (6.5, 1.0)
        axes(d, "x", "y")
        def fit(rows):
            mx, my = (sum(row[j] for row in rows)/len(rows) for j in [0, 1])
            slope = sum((x-mx)*(y-my) for x, y in rows)/sum((x-mx)**2 for x, _ in rows)
            return slope, my-slope*mx
        xy = lambda x, y: (55+52*x, 265-32*y)
        for x, y in points:
            d.circle(*xy(x, y), 4, fill=d.blue, color=d.blue)
        d.circle(*xy(*outlier), 6, fill=d.red, color=d.red)
        for rows, color in [(points, d.blue), (points+[outlier], d.red)]:
            slope, intercept = fit(rows)
            d.poly([xy(x, slope*x+intercept) for x in [.5, 7]], color=color)
        d.text("合成数据示意", 264, 30, size=14)
    elif v == "wheatstone_bridge":
        top, left, right, bottom = (240, 45), (100, 155), (380, 155), (240, 265)
        for a, b in [(top, left), (top, right), (left, bottom), (right, bottom)]:
            d.line(*a, *b)
            cx, cy = (a[0]+b[0])/2, (a[1]+b[1])/2
            resistor = Drawing(40, 20, mono)
            resistor.rect(-20, -9, 40, 18, fill=d.surface)
            d.add(resistor, cx, cy, rotate=math.degrees(math.atan2(b[1]-a[1], b[0]-a[0])))
        d.line(*left, *right)
        dot(d, 240, 155, "G", r=22)
        d.poly([(240, 45), (35, 45), (35, 265), (240, 265)])
        d.rect(22, 128, 26, 58, fill="#fff", color="#fff", width=1)
        d.line(13, 143, 57, 143)
        d.line(22, 161, 48, 161)
        for x, y, label in [(160, 83, "R₁"), (337, 84, "R₂"), (155, 238, "R₃"), (341, 238, "R₄")]:
            d.text(label, x, y, size=15)
        for x, y in [top, left, right, bottom]:
            d.circle(x, y, 3, fill=d.ink)
    elif v == "rc_lowpass_response":
        axes(d, "f / f_c (log)", "|H|", label_x=255, label_y=312)
        pts = []
        for i in range(101):
            ratio = 10**(-2+4*i/100)
            pts.append((55+370*i/100, 265-205/math.sqrt(1+ratio**2)))
        d.poly(pts, color=d.blue, width=3)
        for x, label in [(55, "0.01"), (148, "0.1"), (240, "1"), (333, "10"), (425, "100")]:
            d.text(label, x, 284, size=12)
        d.text("1", 39, 65, size=13)
        d.text("0", 39, 270, size=13)
    elif v == "membrane_separation":
        d.rect(170, 85, 140, 150, fill=d.glass, color=d.blue)
        d.line(240, 90, 240, 230, color=d.gold, width=7)
        for y in range(100, 231, 20):
            d.circle(240, y, 2, fill=d.surface, color=d.surface, width=1)
        d.arrow(40, 155, 166, 155, color=d.blue)
        d.arrow(314, 155, 439, 155, color=d.blue)
        d.poly([(205, 235), (205, 278), (408, 278)], color=d.gold)
        d.arrow(381, 278, 420, 278, color=d.gold)
        d.text("进料", 96, 135, size=15)
        d.text("透过液", 388, 135, size=15)
        d.text("浓缩液", 349, 305, size=15)
        d.text("膜", 265, 71, size=15)
    elif v == "hydroponic_loop":
        d.rect(40, 195, 110, 91, fill=d.glass, color=d.blue)
        d.rect(198, 155, 237, 50, fill=d.glass, color=d.blue)
        for x in [241, 316, 392]:
            d.line(x, 153, x, 93, color=d.green, width=3)
            for side in [-1, 1]:
                d.path(f"M {x} 120 Q {x+side*35} 84 {x+side*37} 112 Q {x+side*18} 130 {x} 120 Z", fill=d.green, color=d.green, width=1)
            for dx in [-9, 0, 9]:
                d.line(x, 157, x+dx, 189, color=d.gold, width=1)
        d.poly([(110, 224), (170, 224), (170, 174), (198, 174)], color=d.blue, width=3)
        dot(d, 170, 220, "P", r=17)
        d.poly([(435, 185), (453, 185), (453, 280), (150, 280)], color=d.blue, width=3)
        d.arrow(426, 280, 380, 280, color=d.blue)
        d.arrow(174, 178, 193, 174, color=d.blue)
        d.text("营养液", 95, 185, size=15)
    else:
        raise DiagramError("diagram_renderer_missing")
    return d
