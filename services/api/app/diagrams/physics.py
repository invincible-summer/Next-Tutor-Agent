"""Readable circuit symbols, optical elements, measurements and wave apparatus."""
from __future__ import annotations

import math

from .drawing import Drawing, num
from .schema import DiagramError


def circuit(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    d.anchors.update({"terminal_left": (12, 80), "terminal_right": (148, 80)})
    if variant in {"battery", "battery_pack", "dc_supply", "ac_supply"}:
        d.line(12, 80, 57, 80)
        d.line(103, 80, 148, 80)
        if variant in {"dc_supply", "ac_supply"}:
            d.circle(80, 80, 27, fill=d.surface)
            if variant == "ac_supply":
                d.path("M 63 80 Q 72 58 80 80 Q 89 102 97 80", color=d.blue)
            else:
                d.text("+", 70, 85, size=19)
                d.text("−", 91, 85, size=19)
        else:
            for i in range(2 if variant == "battery_pack" else 1):
                x = 67+i*23
                d.line(x, 49, x, 111, width=3)
                d.line(x+10, 64, x+10, 96, width=3)
            d.line(57, 80, 67, 80)
            d.line(77 if variant == "battery" else 100, 80, 103, 80)
    elif variant in {"resistor", "rheostat", "potentiometer", "thermistor", "photoresistor", "fuse"}:
        d.line(12, 80, 43, 80)
        d.line(117, 80, 148, 80)
        d.rect(43, 66, 74, 28, fill=d.surface)
        if variant == "potentiometer":
            d.arrow(80, 20, 80, 65)
            d.anchors["wiper"] = (80, 20)
        elif variant == "rheostat":
            d.arrow(41, 115, 119, 44)
        elif variant == "thermistor":
            d.poly([(41, 113), (60, 113), (116, 44)])
        elif variant == "photoresistor":
            d.circle(80, 80, 53)
            d.arrow(25, 20, 52, 50)
            d.arrow(47, 10, 72, 40)
        elif variant == "fuse":
            d.line(43, 80, 117, 80)
    elif variant in {"capacitor", "polar_capacitor"}:
        d.line(12, 80, 69, 80)
        d.line(91, 80, 148, 80)
        d.line(69, 48, 69, 112, width=3)
        if variant == "polar_capacitor":
            d.path("M 97 48 Q 83 80 97 112", width=3)
            d.text("+", 54, 48)
        else:
            d.line(91, 48, 91, 112, width=3)
    elif variant in {"inductor", "transformer", "solenoid"}:
        d.line(12, 80, 40, 80)
        for i in range(4):
            x = 40+i*20
            d.path(f"M {x} 80 C {x-3} 40 {x+23} 40 {x+20} 80")
        d.line(120, 80, 148, 80)
        if variant == "transformer":
            d.line(40, 95, 120, 95)
            d.line(40, 101, 120, 101)
            for i in range(4):
                x = 40+i*20
                d.path(f"M {x} 116 C {x-3} 151 {x+23} 151 {x+20} 116")
            d.anchors.update({"secondary_left": (40, 116), "secondary_right": (120, 116)})
    elif variant in {"lamp_symbol", "motor_symbol", "generator_symbol", "ammeter", "voltmeter", "galvanometer"}:
        d.line(12, 80, 49, 80)
        d.line(111, 80, 148, 80)
        d.circle(80, 80, 31, fill="#fff")
        if variant == "lamp_symbol":
            d.line(59, 59, 101, 101)
            d.line(59, 101, 101, 59)
        else:
            d.text({"motor_symbol": "M", "generator_symbol": "G", "ammeter": "A", "voltmeter": "V", "galvanometer": "G"}[variant], 80, 89, size=27)
    elif variant in {"switch", "double_switch", "wire", "junction", "crossing", "ground", "diode", "led", "transistor", "mosfet", "op_amp", "relay"}:
        if variant in {"switch", "double_switch", "relay"}:
            d.line(12, 80, 49, 80)
            d.circle(52, 80, 3, fill=d.ink)
            d.circle(111, 80, 3, fill=d.ink)
            d.line(54, 78, 107, 80 if p.get("closed", False) else 51)
            d.line(114, 80, 148, 80)
            if variant == "double_switch":
                d.circle(111, 47, 3, fill=d.ink)
                d.line(114, 47, 148, 47)
                d.anchors["terminal_upper"] = (148, 47)
            if variant == "relay":
                d.rect(51, 109, 62, 19, fill=d.surface)
                d.line(82, 106, 82, 80, dashed=True)
        elif variant in {"wire", "junction", "crossing"}:
            d.line(12, 80, 148, 80)
            if variant == "junction":
                d.line(80, 15, 80, 145)
                d.circle(80, 80, 4, fill=d.ink)
            if variant == "crossing":
                d.path("M 80 15 L 80 69 C 63 69 63 91 80 91 L 80 145", color=d.ink)
        elif variant == "ground":
            d.line(80, 20, 80, 89)
            for y, w in [(89, 64), (100, 45), (111, 26)]:
                d.line(80-w/2, y, 80+w/2, y)
        elif variant in {"diode", "led"}:
            d.line(12, 80, 57, 80)
            d.line(101, 80, 148, 80)
            d.poly([(57, 55), (57, 105), (99, 80)], closed=True)
            d.line(101, 54, 101, 106)
            if variant == "led":
                d.arrow(91, 44, 111, 24)
                d.arrow(111, 54, 131, 34)
        elif variant == "op_amp":
            d.poly([(39, 33), (39, 127), (125, 80)], closed=True, fill=d.surface)
            d.line(12, 60, 39, 60)
            d.line(12, 101, 39, 101)
            d.line(125, 80, 148, 80)
            d.text("−", 53, 67)
            d.text("+", 53, 107)
            d.anchors.update({"input_minus": (12, 60), "input_plus": (12, 101)})
        else:
            d.circle(80, 80, 48)
            d.line(12, 80, 65, 80)
            d.line(65, 50, 65, 110, width=3)
            d.poly([(65, 65), (100, 40), (100, 15)])
            d.poly([(65, 96), (100, 121), (100, 145)])
            if variant == "transistor":
                d.arrow(79, 106, 98, 120)
            else:
                d.line(74, 48, 74, 113)
            d.anchors.update({"collector": (100, 15), "emitter": (100, 145), "gate": (12, 80)})
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


def optics(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"convex_lens", "concave_lens", "plane_mirror", "concave_mirror", "convex_mirror", "interface", "screen"}:
        if variant == "convex_lens":
            d.path("M 80 15 Q 113 80 80 145 Q 47 80 80 15 Z", fill=d.glass, color=d.blue)
        elif variant == "concave_lens":
            d.path("M 61 15 L 99 15 Q 75 80 99 145 L 61 145 Q 85 80 61 15 Z", fill=d.glass, color=d.blue)
        elif variant in {"plane_mirror", "interface", "screen"}:
            d.line(80, 14, 80, 146, width=3)
            if variant == "plane_mirror":
                for y in range(20, 141, 12):
                    d.line(80, y, 94, y-7, color=d.muted, width=1)
            if variant == "screen":
                d.rect(74, 14, 12, 125, fill=d.surface)
                d.poly([(80, 139), (80, 151), (55, 151), (105, 151)])
        else:
            control = 119 if variant == "concave_mirror" else 41
            d.path(f"M 71 15 Q {control} 80 71 145", width=3)
            d.path(f"M 78 15 Q {control+7} 80 78 145", color=d.muted)
        d.anchors["optical_center"] = (80, 80)
    elif variant in {"prism", "glass_brick", "double_slit", "grating", "pinhole", "aperture"}:
        if variant == "prism":
            d.poly([(25, 129), (135, 129), (80, 28)], closed=True, fill=d.glass, color=d.blue)
        elif variant == "glass_brick":
            d.rect(29, 45, 103, 74, fill=d.glass, color=d.blue)
            d.poly([(29, 45), (44, 31), (147, 31), (132, 45)], closed=True, fill=d.surface, color=d.blue)
            d.poly([(132, 45), (147, 31), (147, 105), (132, 119)], closed=True, fill=d.glass, color=d.blue)
        else:
            d.rect(70, 15, 20, 130, fill=d.surface)
            ys = [77] if variant in {"pinhole", "aperture"} else [65, 91] if variant == "double_slit" else list(range(30, 136, 13))
            for y in ys:
                d.rect(68, y, 24, 5, fill="#fff", color="#fff", width=.5)
    elif variant in {"candle", "light_source", "laser", "optical_bench", "camera", "telescope", "eye", "optical_fiber"}:
        if variant == "candle":
            d.rect(67, 65, 26, 72, fill=d.surface, radius=3)
            d.line(80, 65, 80, 52)
            d.path("M 80 24 C 53 48 67 59 80 59 C 96 53 99 38 80 24 Z", fill=d.gold, color=d.gold)
            d.anchors["source"] = (80, 41)
        elif variant == "light_source":
            d.circle(80, 80, 21, fill=d.gold)
            for i in range(8):
                a = i*math.pi/4
                d.line(80+32*math.cos(a), 80+32*math.sin(a), 80+52*math.cos(a), 80+52*math.sin(a), color=d.gold)
        elif variant == "laser":
            d.rect(15, 58, 94, 43, fill=d.surface, radius=4)
            d.rect(109, 69, 14, 21, fill=d.ink)
            d.arrow(123, 80, 153, 80, color=d.red)
        elif variant == "optical_bench":
            d.poly([(15, 99), (133, 99), (147, 111), (25, 111)], closed=True, fill=d.surface)
            for x in range(25, 140, 10):
                d.line(x, 100, x, 105, width=1)
            for x in [35, 83, 126]:
                d.line(x, 99, x, 55, width=3)
                d.ellipse(x, 44, 12, 22, fill=d.glass, color=d.blue)
        elif variant == "eye":
            d.path("M 15 80 Q 80 8 145 80 Q 80 152 15 80 Z", fill="#fff")
            d.circle(80, 80, 29, fill=d.blue)
            d.circle(80, 80, 12, fill=d.ink)
        elif variant == "camera":
            d.rect(20, 51, 119, 77, fill=d.surface, radius=9)
            d.rect(39, 35, 37, 18, fill=d.surface, radius=3)
            d.circle(86, 89, 29, fill=d.glass)
            d.circle(86, 89, 19)
            d.rect(27, 63, 19, 11, fill=d.blue, radius=2)
        elif variant == "telescope":
            d.poly([(20, 54), (128, 34), (139, 72), (31, 92)], closed=True, fill=d.surface)
            d.ellipse(133, 53, 10, 20, fill=d.glass)
            d.line(78, 82, 78, 143, width=3)
            d.line(78, 104, 40, 145)
            d.line(78, 104, 117, 145)
        else:
            d.path("M 13 108 C 44 15 88 144 147 46", width=7, color=d.blue)
            d.path("M 13 108 C 44 15 88 144 147 46", width=2, color="#fff")
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


def measurement(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"meter", "ammeter_real", "voltmeter_real", "multimeter", "pressure_gauge", "dynamometer", "compass"}:
        if variant == "dynamometer":
            d.rect(61, 28, 38, 104, fill=d.surface, radius=7)
            d.path("M 80 8 Q 63 8 68 23 L 80 28 M 80 132 L 80 143 Q 99 145 87 154")
            reading, maximum = p.get("reading", 0), p.get("maximum", 5)
            if not 0 <= reading <= maximum:
                raise DiagramError("diagram_invalid_reading")
            for i in range(11):
                y = 43+i*7.2
                d.line(70, y, 79 if i % 2 else 86, y, width=1)
                if p.get("scale_labels", False) and i % 2 == 0:
                    d.text(f"{maximum*i/10:g}", 55, y+3, size=11, anchor="end")
            y = 43+reading/maximum*72
            d.poly([(95, y-4), (85, y), (95, y+4)], closed=True, color=d.red, fill=d.red)
            d.anchors["hook"] = (80, 154)
            d.facts.update({"reading": reading, "maximum": maximum})
        elif variant == "compass":
            d.circle(80, 80, 58, fill=d.surface)
            d.circle(80, 80, 50, fill="#fff", width=1)
            d.poly([(80, 29), (92, 80), (80, 131), (68, 80)], closed=True, fill=d.blue)
            d.poly([(80, 29), (92, 80), (68, 80)], closed=True, fill=d.red)
        else:
            d.rect(20, 31, 120, 110, fill=d.surface, radius=12)
            d.path("M 35 91 A 48 48 0 0 1 125 91", fill="#fff")
            maximum = p.get("maximum", 5)
            reading = p.get("reading", 0)
            for i in range(11):
                a = math.pi+i*math.pi/10
                d.line(80+44*math.cos(a), 96+44*math.sin(a), 80+(36 if i % 2 == 0 else 40)*math.cos(a), 96+(36 if i % 2 == 0 else 40)*math.sin(a), width=1)
                if p.get("scale_labels", False) and i % 2 == 0:
                    d.text(f"{maximum*i/10:g}", 80+29*math.cos(a), 101+29*math.sin(a), size=11)
            if not 0 <= reading <= maximum:
                raise DiagramError("diagram_invalid_reading")
            a = math.pi+reading/maximum*math.pi
            d.line(80, 96, 80+41*math.cos(a), 96+41*math.sin(a), color=d.red, width=1.7)
            d.circle(80, 96, 3, fill=d.ink)
            for x, color in [(44, d.red), (116, d.ink)]:
                d.circle(x, 123, 6, fill=color)
            if variant in {"ammeter_real", "voltmeter_real"}:
                d.text("A" if variant == "ammeter_real" else "V", 80, 121, size=16)
            d.anchors.update({"terminal_left": (44, 123), "terminal_right": (116, 123)})
            d.facts.update({"reading": reading, "maximum": maximum})
    elif variant in {"ruler", "protractor", "vernier", "micrometer", "dial_gauge", "stopwatch", "ticker_timer", "photogate"}:
        if variant == "protractor":
            d.path("M 15 121 A 65 65 0 0 1 145 121 Z", fill=d.glass)
            for i in range(19):
                a = math.pi+i*math.pi/18
                d.line(80+62*math.cos(a), 121+62*math.sin(a), 80+(52 if i % 3 == 0 else 57)*math.cos(a), 121+(52 if i % 3 == 0 else 57)*math.sin(a), width=1)
        elif variant in {"ruler", "vernier"}:
            d.rect(12, 58, 136, 35, fill=d.surface, radius=2)
            for i in range(27):
                d.line(15+i*5, 59, 15+i*5, 75 if i % 5 == 0 else 68, width=1)
            if variant == "vernier":
                d.poly([(13, 58), (13, 30), (24, 30), (24, 59)])
                shift = p.get("reading", 15)
                d.rect(35+shift, 80, 59, 25, fill=d.glass)
                d.poly([(35+shift, 81), (35+shift, 29), (46+shift, 29), (46+shift, 58)])
                for i in range(11):
                    d.line(40+shift+i*4.5, 82, 40+shift+i*4.5, 92, width=1)
        elif variant == "micrometer":
            d.path("M 65 52 C 8 33 8 133 65 115", width=12, color=d.muted)
            d.rect(58, 58, 38, 16, fill=d.surface)
            d.rect(96, 50, 33, 32, fill=d.glass, radius=2)
            d.rect(129, 57, 21, 18, fill=d.surface)
            for i in range(5):
                d.line(100+i*5, 53, 100+i*5, 65, width=1)
        elif variant in {"stopwatch", "dial_gauge"}:
            d.circle(80, 90, 48, fill=d.surface)
            d.circle(80, 90, 40, fill="#fff", width=1)
            d.rect(70, 21, 20, 15, fill=d.surface, radius=3)
            for i in range(12):
                a = i*math.pi/6
                d.line(80+33*math.sin(a), 90-33*math.cos(a), 80+39*math.sin(a), 90-39*math.cos(a), width=1)
            d.line(80, 90, 106, 66, color=d.red)
        else:
            d.rect(25, 49, 111, 69, fill=d.surface, radius=7)
            if variant == "photogate":
                d.rect(62, 48, 37, 51, fill="#fff", color="#fff")
                d.line(57, 68, 104, 68, dashed=True, color=d.red)
            else:
                d.rect(45, 65, 75, 22, fill=d.glass)
                d.line(30, 107, 145, 107, color=d.gold, width=5)
    elif variant in {"balance", "electronic_balance", "hotplate", "stirrer", "water_bath", "heating_mantle", "ph_meter", "conductivity_meter", "oscilloscope", "signal_generator", "power_supply", "spectrophotometer", "centrifuge", "drying_oven"}:
        if variant == "balance":
            d.rect(47, 135, 66, 9, fill=d.surface, radius=3)
            d.poly([(80, 135), (67, 119), (80, 47), (93, 119)], closed=True, fill=d.surface)
            d.line(24, 48, 136, 48, width=3)
            for x in [31, 129]:
                d.line(x, 48, x-21, 103, width=1)
                d.line(x, 48, x+21, 103, width=1)
                d.path(f"M {x-26} 103 Q {x} 127 {x+26} 103 Z", fill=d.surface)
        else:
            d.rect(16, 54, 128, 85, fill=d.surface, radius=9)
            if variant == "spectrophotometer":
                # A sample compartment beside a readout, rather than a scope
                # waveform. The cuvette is visible in the open compartment.
                d.rect(27, 65, 49, 52, fill=d.glass, radius=3)
                d.line(28, 78, 75, 78, color=d.muted, width=1)
                d.line(42, 71, 61, 71, width=2)
                d.rect(38, 87, 25, 24, fill=d.ink, radius=2)
                d.rect(45, 87, 11, 20, fill=d.glass, width=1)
                d.line(45, 98, 56, 98, color=d.blue, width=1)
                d.rect(84, 66, 47, 25, fill=d.glass, radius=3)
                for x in [92, 103, 114]:
                    d.line(x, 74, x+5, 74, color=d.blue, width=2)
                    d.line(x, 82, x+5, 82, color=d.blue, width=2)
                d.circle(91, 111, 6, fill="#fff")
                d.circle(118, 111, 8, fill="#fff")
                d.anchors["sample_compartment"] = (51, 99)
            elif variant in {"oscilloscope", "signal_generator"}:
                d.rect(27, 66, 78, 54, fill=d.ink, radius=4)
                d.poly([(31+i*3.3, 93+15*math.sin(i*.4)) for i in range(21)], color=d.green, width=1.7)
                for x, y in [(124, 80), (124, 104)]:
                    d.circle(x, y, 6, fill="#fff")
            elif variant in {"hotplate", "stirrer", "water_bath", "heating_mantle", "electronic_balance"}:
                d.ellipse(80, 50, 55, 12, fill=d.surface)
                d.rect(39, 91, 48, 23, fill=d.glass, radius=3)
                d.circle(114, 102, 8, fill="#fff")
                if variant == "heating_mantle":
                    d.path("M 28 50 Q 80 128 132 50", fill=d.surface)
                if variant in {"stirrer", "water_bath"}:
                    from .instruments import vessel
                    if variant == "water_bath":
                        d.rect(40, 17, 80, 45, fill=d.glass, color=d.blue)
                        d.line(41, 39, 119, 39, color=d.blue)
                    d.add(vessel("beaker", {"fill": .45}, mono), 48, -4, .4)
                    if variant == "stirrer":
                        d.line(75, 46, 85, 46, color=d.ink, width=2)
            elif variant == "centrifuge":
                d.ellipse(80, 51, 56, 13, fill=d.glass)
                d.ellipse(80, 28, 56, 13, fill=d.surface)
                d.circle(80, 87, 20)
                for i in range(6):
                    a = i*math.pi/3
                    d.circle(80+15*math.cos(a), 87+15*math.sin(a), 3, fill=d.blue)
            elif variant == "drying_oven":
                d.rect(30, 67, 70, 58, fill=d.glass, radius=3)
                d.line(107, 69, 107, 124, width=3)
            else:
                d.rect(29, 66, 68, 34, fill=d.glass, radius=3)
                d.circle(122, 80, 7, fill="#fff")
                d.poly([(139, 100), (151, 100), (151, 33), (123, 33), (123, 17)])
                d.anchors["probe"] = (123, 17)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d


def waves(variant: str, p: dict, mono=False) -> Drawing:
    d = Drawing(monochrome=mono)
    if variant in {"transverse", "standing_wave", "longitudinal", "wavefront", "wave_source", "tuning_fork", "speaker", "resonance_tube", "air_column", "water_wave_tank", "vibrating_membrane"}:
        if variant in {"transverse", "standing_wave"}:
            d.line(10, 80, 150, 80, dashed=True, color=d.muted)
            d.poly([(10+i*1.4, 80+28*math.sin(i*math.pi/25)) for i in range(101)], color=d.blue)
            if variant == "standing_wave":
                d.poly([(10+i*1.4, 80-28*math.sin(i*math.pi/25)) for i in range(101)], color=d.muted)
        elif variant == "longitudinal":
            for i in range(32):
                x = 10+i*4.4+3*math.sin(i*.6)
                d.ellipse(x, 80, 3, 24, width=1)
        elif variant in {"wavefront", "wave_source"}:
            d.circle(80, 80, 3, fill=d.ink)
            for r in [15, 30, 45, 60]:
                d.circle(80, 80, r, color=d.blue, width=1.3)
        elif variant == "tuning_fork":
            d.path("M 48 19 L 48 77 Q 48 105 80 105 Q 112 105 112 77 L 112 19", width=7, color=d.muted)
            d.line(80, 105, 80, 147, width=7)
        elif variant in {"resonance_tube", "air_column"}:
            d.rect(61, 17, 38, 125, fill=d.glass)
            d.rect(61, 109, 38, 33, fill=d.blue)
            d.poly([(75+i*10/40, 25+i*2) for i in range(41)], color=d.muted)
        elif variant == "water_wave_tank":
            d.poly([(15, 41), (120, 29), (145, 108), (40, 131)], closed=True, fill=d.glass)
            for y in range(53, 114, 14):
                d.line(35, y, 120, y-12, color=d.blue, width=1)
        elif variant == "vibrating_membrane":
            d.ellipse(80, 84, 56, 34, fill=d.surface)
            d.ellipse(80, 80, 45, 23, color=d.blue)
        else:
            d.rect(30, 34, 68, 97, fill=d.surface, radius=7)
            d.circle(64, 99, 22, fill=d.ink)
            d.circle(64, 56, 10, fill=d.ink)
            for x in [113, 126, 139]:
                d.path(f"M {x} 50 Q {x+20} 80 {x} 110", color=d.blue)
    elif variant in {"magnet", "horseshoe_magnet", "electromagnet", "field_lines", "into_page", "out_of_page"}:
        if variant in {"into_page", "out_of_page"}:
            d.circle(80, 80, 36)
            if variant == "into_page":
                d.line(62, 62, 98, 98)
                d.line(62, 98, 98, 62)
            else:
                d.circle(80, 80, 5, fill=d.ink)
        elif variant == "horseshoe_magnet":
            d.path("M 29 29 L 29 100 Q 80 164 131 100 L 131 29 L 103 29 L 103 91 Q 80 119 57 91 L 57 29 Z", fill=d.surface)
            d.rect(29, 29, 28, 27, fill=d.red)
            d.rect(103, 29, 28, 27, fill=d.blue)
        else:
            d.rect(25, 61, 110, 39, fill=d.blue)
            d.rect(25, 61, 55, 39, fill=d.red)
            if p.get("show_poles", False):
                d.text("N", 52, 88, color="#fff")
                d.text("S", 108, 88, color="#fff")
            if variant == "field_lines":
                for rise in [20, 40, 57]:
                    d.path(f"M 28 62 C 0 {61-rise} 160 {61-rise} 132 62", color=d.muted, width=1)
                    d.path(f"M 28 100 C 0 {100+rise} 160 {100+rise} 132 100", color=d.muted, width=1)
            if variant == "electromagnet":
                for x in range(39, 126, 12):
                    d.path(f"M {x} 57 Q {x-9} 80 {x} 104", color=d.gold)
    elif variant in {"syringe", "piston", "hydraulic_press", "manometer", "communicating_vessels", "gas_container", "particle_container"}:
        if variant in {"syringe", "piston"}:
            d.rect(36, 50, 85, 53, fill=d.glass)
            d.rect(42+p.get("displacement", 10), 52, 6, 49, fill=d.surface)
            d.line(10, 76, 45+p.get("displacement", 10), 76, width=5)
            d.line(10, 62, 10, 90, width=3)
            d.poly([(121, 63), (135, 63), (135, 91), (121, 91)], fill=d.glass)
            d.line(135, 77, 151, 77)
            d.anchors["tube_outlet"] = (151, 77)
        elif variant == "manometer":
            d.path("M 41 18 L 41 113 Q 41 139 80 139 Q 119 139 119 113 L 119 18", color=d.muted, width=8)
            d.path("M 41 87 L 41 113 Q 41 139 80 139 Q 119 139 119 113 L 119 65", color=d.blue, width=4)
        elif variant in {"hydraulic_press", "communicating_vessels"}:
            d.poly([(25, 25), (25, 129), (140, 129), (140, 40), (95, 40), (95, 106), (48, 106), (48, 25)], fill=d.glass)
            d.poly([(28, 71), (28, 125), (137, 125), (137, 71), (98, 71), (98, 109), (45, 109), (45, 71)], fill=d.blue, color=d.blue)
            if variant == "hydraulic_press":
                d.line(24, 68, 48, 68, width=6)
                d.line(96, 68, 140, 68, width=6)
                d.line(36, 68, 36, 32, width=4)
                d.line(118, 68, 118, 30, width=4)
        else:
            d.rect(19, 25, 122, 111, fill=d.glass, radius=4)
            for i in range(p.get("count", 12)):
                x, y = 31+(i*31 % 100), 40+(i*23 % 82)
                d.circle(x, y, 4, fill=d.blue)
    else:
        raise DiagramError("diagram_unknown_variant")
    return d
