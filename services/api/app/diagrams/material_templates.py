"""Original SVG examples and editable native primitives."""

def wrap(content):
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400">' + content + '</svg>'


TEMPLATES = [
    {"id": "geometry", "title": "几何构图 / Geometry", "subject": "mathematics",
     "description": "圆与内接正三角形，调整坐标和半径；示意图未按物理单位标定。",
     "svg": wrap('<circle id="circle" cx="320" cy="200" r="140" fill="none" stroke="#26364a" stroke-width="2"/>'
        '<polygon id="triangle" points="320,60 441.244,270 198.756,270" fill="none" stroke="#26364a" stroke-width="2"/>')},
    {"id": "beaker", "title": "容器与液面 / Vessel", "subject": "chemistry",
     "description": "透明容器、定性液面与文字；不提供温度或体积刻度。",
     "svg": wrap('<path id="water" d="M 210 210 H 430 L 420 330 H 220 Z" fill="#a9cedb" stroke="none"/>'
        '<path id="vessel" d="M 200 90 L 220 330 H 420 L 440 90" fill="none" stroke="#26364a" stroke-width="3"/>'
        '<line id="surface" x1="210" y1="210" x2="430" y2="210" stroke="#5a8da7" stroke-width="2"/>')},
    {"id": "flow", "title": "流程与关系 / Flow", "subject": "general",
     "description": "两个矩形节点和连接线，可增删节点、修改文字与方向。",
     "svg": wrap('<rect id="node1" x="70" y="150" width="180" height="90" rx="8" fill="#e2f0f5" stroke="#26364a"/>'
        '<text id="label1" x="160" y="202" text-anchor="middle" font-size="22">过程一</text>'
        '<line id="connection" x1="250" y1="195" x2="390" y2="195" stroke="#26364a"/>'
        '<polyline points="380,187 390,195 380,203" fill="none" stroke="#26364a"/>'
        '<rect id="node2" x="390" y="150" width="180" height="90" rx="8" fill="#e2f0f5" stroke="#26364a"/>'
        '<text id="label2" x="480" y="202" text-anchor="middle" font-size="22">过程二</text>')},
    {"id": "design", "title": "基础图元 / Primitives", "subject": "general",
     "description": "矩形、圆、直线和文本的完整SVG样例，可直接改坐标。",
     "svg": wrap('<rect id="rectangle" x="90" y="130" width="170" height="120" fill="#e2f0f5" stroke="#26364a"/>'
        '<circle id="circle" cx="440" cy="190" r="65" fill="none" stroke="#26364a"/>'
        '<text id="label" x="320" y="330" text-anchor="middle" font-size="22">基础图元样例</text>')},
]


# The same declaration is editable, validated and used by the quiz compiler.
TEMPLATES[0]["parameterization"] = {
    "schema_version": "1.0.0",
    "parameters": {"radius": {"type": "number", "role": "schematic", "unit": "diagram_px",
        "description": "圆和内接正三角形的示意半径，统一联动", "minimum": 80, "maximum": 160, "default": 140}},
    "bindings": [{"parameter": "radius", "element_id": "circle", "attribute": "r"}],
}
# Points/path expressions are deliberately not programmable. Three SVG lines
# preserve the exact inscribed construction under the radius control.
TEMPLATES[0]["svg"] = wrap('<circle id="circle" cx="320" cy="200" r="140" fill="none" stroke="#26364a" stroke-width="2"/>' +
    ''.join(f'<line id="side{i}" x1="320" y1="60" x2="441.244" y2="270" stroke="#26364a" stroke-width="2"/>' for i in range(3)))
points = [(0, -1), (.8660254037844386, .5), (-.8660254037844386, .5)]
for i in range(3):
    for suffix, (fx, fy) in zip(("1", "2"), (points[i], points[(i+1)%3])):
        for axis, factor, offset in (("x", fx, 320), ("y", fy, 200)):
            TEMPLATES[0]["parameterization"]["bindings"].append({"parameter": "radius", "element_id": f"side{i}",
                "attribute": axis+suffix, "factor": factor, "offset": offset})
from .svg_bindings import program
TEMPLATES[0]["svg"] = program(TEMPLATES[0]["parameterization"]).apply(TEMPLATES[0]["svg"])[0]
TEMPLATES[2]["parameterization"] = {"schema_version": "1.0.0", "parameters": {
    "left_text": {"type": "string", "role": "text", "description": "流程第一步的题面文字", "default": "过程一", "max_length": 6},
    "right_text": {"type": "string", "role": "text", "description": "流程第二步的题面文字", "default": "过程二", "max_length": 6}},
    "bindings": [{"parameter": key, "element_id": element, "attribute": "text"}
        for key, element in (("left_text", "label1"), ("right_text", "label2"))]}
for template in TEMPLATES:
    template.setdefault("parameterization", {"schema_version": "1.0.0", "parameters": {}, "bindings": []})

# Small generic primitive examples for AI authoring, independent of subjects.
CONTROL_EXAMPLES = [
    {"svg": wrap('<circle id="c" cx="320" cy="200" r="100" fill="none" stroke="#26364a"/>'
        '<line id="l" x1="320" y1="200" x2="420" y2="200" stroke="#26364a"/>'),
     "parameterization": {"schema_version": "1.0.0", "parameters": {"size": {
        "type": "number", "role": "schematic", "unit": "diagram_px", "description": "联动的示意尺寸",
        "default": 100, "minimum": 80, "maximum": 140}}, "bindings": [
            {"parameter": "size", "element_id": "c", "attribute": "r"},
            {"parameter": "size", "element_id": "l", "attribute": "x2", "offset": 320}]}},
    {"svg": wrap('<text id="t" x="320" y="200" font-size="22">过程</text>'),
     "parameterization": {"schema_version": "1.0.0", "parameters": {"label": {
        "type": "string", "role": "text", "description": "可替换文字", "default": "过程", "max_length": 8}},
        "bindings": [{"parameter": "label", "element_id": "t", "attribute": "text"}]}}
]
