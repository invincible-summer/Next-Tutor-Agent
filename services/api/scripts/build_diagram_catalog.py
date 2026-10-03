"""Rebuild the shipped catalog from the original, runnable drawing inventory.

Run from any directory. --check checks reproducibility without editing resources.
Every entry points at a registered renderer, and preview validation checks the art.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
INVENTORY = {
"geometry": """
point|点|point
segment|线段|line segment
line|直线|straight line
ray|射线|ray
polyline|折线|polyline
arrow|单向箭头|arrow|力箭头,速度箭头,加速度箭头,方向标记,引出线
double_arrow|双向箭头|double arrow
dimension|尺寸线|dimension line|力臂,位移线
parallel|平行标记|parallel marks
perpendicular|垂直标记|perpendicular marks|垂足
equal_length|等长刻痕|congruence marks
right_angle|直角标记|right angle
brace|花括号|brace
bracket|括号|bracket
break|断裂线|break line
triangle|任意三角形|triangle|三角形
right_triangle|直角三角形|right triangle
isosceles_triangle|等腰三角形|isosceles triangle
equilateral_triangle|等边三角形|equilateral triangle
rectangle|矩形|rectangle
square|正方形|square
parallelogram|平行四边形|parallelogram
rhombus|菱形|rhombus
trapezoid|梯形|trapezoid
kite|风筝形|kite
polygon|任意多边形|polygon
regular_polygon|正多边形|regular polygon
circle|圆|circle
ellipse|椭圆|ellipse
arc|圆弧|arc|角度弧,相等角弧
sector|扇形|sector
segment_area|弓形|circular segment
annulus|圆环|annulus
chord|弦|chord
tangent|圆的切线|circle tangent|切线,切点
secant|割线|secant
central_angle|圆心角|central angle
inscribed_angle|圆周角|inscribed angle
incircle|内切圆|incircle
circumcircle|外接圆|circumcircle
intersecting_circles|两圆相交|intersecting circles
cube|正方体|cube
cuboid|长方体|cuboid
prism|棱柱|prism
pyramid|棱锥|pyramid
frustum|棱台|frustum
cylinder|圆柱|cylinder
cone|圆锥|cone
cone_frustum|圆台|truncated cone
sphere|球|sphere
hemisphere|半球|hemisphere
hollow_cylinder|空心圆柱|hollow cylinder
section|几何体截面|solid section
net|几何体展开图|solid net
three_views|三视图|orthographic views
grid|网格|grid
hatching|剖面线|hatching|阴影区域
number_line|数轴|number line
fraction_bar|分数条|fraction bar
fraction_circle|分数圆|fraction circle
percent_grid|百分格|hundred grid
dot_array|点阵|dot array
counting_rods|计数小棒|counting rods
base_ten|十进制积木|base ten blocks|计数方块
abacus|算盘|abacus
clock|钟面|clock face
dice|骰子|dice
coin|硬币|coin
spinner|转盘|spinner
matrix|矩阵格|matrix grid
""",
"function": """
axes|平面直角坐标系|Cartesian axes
polar|极坐标网格|polar axes
complex|复平面|complex plane
linear|一次函数|linear function
quadratic|二次函数|quadratic function
polynomial|多项式函数|polynomial function
reciprocal|反比例函数|reciprocal function
absolute|绝对值函数|absolute value
sqrt|根式函数|square root function
exponential|指数函数|exponential function
logarithm|对数函数|logarithmic function
sine|正弦函数|sine
cosine|余弦函数|cosine
tangent|正切函数|tangent function
tangent_line|函数与切线|function tangent
integral|积分区域|integral area
riemann|黎曼矩形|Riemann rectangles
vector|平面向量|vector
vector_sum|向量和|vector sum
projection|向量投影|vector projection
basis|基向量|basis vectors
linear_transform|线性变换网格|linear transformation
""",
"chart": """
bar|柱状图|bar chart|竖向柱状图
horizontal_bar|条形图|horizontal bar chart
grouped|分组柱状图|grouped bar chart
stacked|堆叠柱状图|stacked bar chart
percent_stacked|百分比堆叠图|percent stacked chart
line|折线图|line chart
multi_line|多系列折线图|multi line chart
area|面积图|area chart
stacked_area|堆叠面积图|stacked area chart
scatter|散点图|scatter plot
bubble|气泡图|bubble chart
pie|饼图|pie chart|扇形统计图,扇形图
donut|环形图|donut chart
histogram|直方图|histogram
frequency_polygon|频数折线|frequency polygon
ogive|累计频率曲线|ogive
ecdf|经验分布函数|empirical distribution
box|箱线图|box plot
dot|点图|dot plot
stem_leaf|茎叶图|stem and leaf
errorbar|误差棒|error bar
confidence|置信区间图|confidence interval
normal|正态分布曲线|normal distribution
t_distribution|t分布曲线|Student t distribution
density|密度图|density plot
qq|Q-Q图|QQ plot
heatmap|热力图|heatmap
lollipop|棒棒糖图|lollipop plot
waterfall|瀑布图|waterfall chart
radar|雷达图|radar chart
funnel|漏斗图|funnel chart
pictograph|象形统计图|pictograph
population|人口金字塔|population pyramid
""",
"vessel": """
beaker|烧杯|beaker|盛液烧杯
tall_beaker|高型烧杯|tall beaker
test_tube|普通试管|test tube|试管
hard_test_tube|硬质试管|boiling tube
sidearm_test_tube|带支管试管|side arm test tube
centrifuge_tube|离心管|centrifuge tube
conical_flask|锥形瓶|conical flask|三角烧瓶,Erlenmeyer flask
flat_flask|平底烧瓶|flat bottom flask
round_flask|圆底烧瓶|round bottom flask
distilling_flask|蒸馏烧瓶|distilling flask|带侧管烧瓶
three_neck_flask|三颈烧瓶|three neck flask
volumetric_flask|容量瓶|volumetric flask
cylinder|量筒|graduated cylinder
measuring_cup|量杯|measuring cup
gas_jar|集气瓶|gas jar
reagent_bottle|细口瓶|reagent bottle
wide_bottle|广口瓶|wide mouth bottle
amber_bottle|棕色试剂瓶|amber bottle
drop_bottle|滴瓶|drop bottle
gas_wash_bottle|洗气瓶|gas wash bottle
wash_bottle|洗瓶|wash bottle
tank|水槽|water tank
bell_jar|玻璃钟罩|bell jar
desiccator|干燥器|desiccator
evaporating_dish|蒸发皿|evaporating dish
crucible|坩埚|crucible
mortar|研钵|mortar
watch_glass|表面皿|watch glass
petri_dish|培养皿|Petri dish
overflow_cup|溢水杯|overflow vessel
calorimeter|量热杯|calorimeter
cup|杯|cup
bucket|桶|bucket
""",
"apparatus": """
alcohol_lamp|酒精灯|alcohol lamp
bunsen_burner|本生灯|Bunsen burner
lamp_cap|灯帽|lamp cap
tripod|三脚架|tripod
wire_mesh|金属网|wire gauze|陶土网,石棉网
stand|铁架台|retort stand
iron_ring|铁圈|iron ring
clamp|铁夹|clamp|万能夹
burette_clamp|滴定管夹|burette clamp
tube_holder|试管夹|test tube holder
tube_rack|试管架|test tube rack
funnel|普通漏斗|funnel|漏斗
thistle_funnel|长颈漏斗|thistle funnel
separatory_funnel|分液漏斗|separatory funnel
dropping_funnel|滴液漏斗|dropping funnel|恒压滴液漏斗
buchner_funnel|布氏漏斗|Buchner funnel
filter_paper|滤纸|filter paper
filter_fold|折叠滤纸|folded filter
pipette|移液管|volumetric pipette
graduated_pipette|刻度吸管|graduated pipette
dropper|胶头滴管|dropper|巴斯德吸管
burette|酸式滴定管|burette|滴定管
base_burette|碱式滴定管|base burette
micropipette|移液器|micropipette
thermometer|温度计|thermometer|温度探头
glass_rod|玻璃棒|glass rod
pestle|研杵|pestle
spatula|药匙|spatula
combustion_spoon|燃烧匙|combustion spoon
condenser|直形冷凝管|Liebig condenser|冷凝管
allihn_condenser|球形冷凝管|Allihn condenser
graham_condenser|蛇形冷凝管|Graham condenser
air_condenser|空气冷凝管|air condenser
stopper|橡皮塞|stopper|玻璃塞
one_hole_stopper|单孔塞|one hole stopper
two_hole_stopper|双孔塞|two hole stopper
tube|直导管|glass tube|连接管
bent_tube|弯导管|bent tube
u_tube|U形管|U tube
tee_tube|三通导管|tee tube
hose|橡胶管|rubber hose
drying_tube|干燥管|drying tube
tongs|坩埚钳|crucible tongs
tweezers|镊子|tweezers
tube_brush|试管刷|test tube brush
stir_bar|搅拌子|stir bar
weighing_paper|称量纸|weighing paper
""",
"mechanics": """
block|滑块|block|木块,小块
metal_block|金属块|metal block
cart|带轮小车|cart|小车
collision_cart|碰撞小车|collision cart
ball|小球|ball
hollow_ball|空心球|hollow ball
disc|圆盘|disc
ring|圆环物体|ring object
pendulum_bob|摆球|pendulum bob|摆锤
spring|水平弹簧|spring|弹簧
vertical_spring|竖直弹簧|vertical spring
spring_oscillator|水平弹簧振子|horizontal oscillator
vertical_oscillator|竖直弹簧振子|vertical oscillator
pendulum|单摆|pendulum
physical_pendulum|物理摆|physical pendulum
double_pendulum|双摆|double pendulum
pulley|固定滑轮|fixed pulley|滑轮
moving_pulley|动滑轮|moving pulley
wheel_axle|轮轴|wheel axle
plane|水平面|horizontal plane|光滑面
rough_plane|粗糙面|rough plane
incline|斜面|inclined plane|可调斜面
wall|固定墙|wall
rail|直轨|rail
step|台阶|step
table|桌边|table edge
lever|杠杆|lever
rod|刚性杆|rigid rod
plate|薄板|plate
weight|砝码|weight
hook|挂钩|hook
rope|细绳|rope
slack_rope|松弛绳|slack rope
trajectory|运动轨迹|trajectory
circular_track|圆轨|circular track|弯轨
conveyor|传送带|conveyor
""",
"circuit": """
battery|单节电池符号|battery symbol|单节电池
battery_pack|电池组符号|battery pack
dc_supply|直流电源符号|DC supply
ac_supply|交流电源符号|AC supply
resistor|电阻符号|resistor
rheostat|变阻器符号|rheostat
potentiometer|电位器|potentiometer
thermistor|热敏电阻|thermistor
photoresistor|光敏电阻|photoresistor
fuse|保险丝|fuse
capacitor|电容|capacitor
polar_capacitor|有极电容|polar capacitor
inductor|电感|inductor
transformer|变压器符号|transformer symbol
solenoid|螺线管|solenoid
lamp_symbol|灯泡符号|lamp symbol
motor_symbol|电动机符号|motor symbol
generator_symbol|发电机符号|generator symbol
ammeter|电流表符号|ammeter symbol
voltmeter|电压表符号|voltmeter symbol
galvanometer|检流计符号|galvanometer
switch|单刀开关|switch|开关
double_switch|双掷开关|double throw switch
relay|继电器|relay
wire|导线|wire
junction|连接点|junction
crossing|不连接交叉线|wire crossing
ground|接地|ground
diode|二极管|diode
led|LED|light emitting diode
transistor|三极管|transistor
mosfet|MOS管|MOSFET
op_amp|运算放大器|op amp
""",
"optics": """
convex_lens|凸透镜|convex lens
concave_lens|凹透镜|concave lens
plane_mirror|平面镜|plane mirror
concave_mirror|凹面镜|concave mirror
convex_mirror|凸面镜|convex mirror
interface|反射折射界面|optical interface
screen|光屏|screen
prism|三棱镜|prism
glass_brick|玻璃砖|glass brick
double_slit|双缝|double slit
grating|光栅|diffraction grating
pinhole|小孔|pinhole
aperture|光阑|aperture
candle|蜡烛|candle
light_source|点光源|light source|平行光源
laser|激光器|laser
optical_bench|光具座|optical bench
camera|照相机|camera
telescope|望远镜|telescope
eye|眼睛示意|eye
optical_fiber|光纤|optical fiber
""",
"measurement": """
meter|模拟仪表|analogue meter
ammeter_real|电流表|ammeter
voltmeter_real|电压表|voltmeter
multimeter|多用电表|multimeter
pressure_gauge|压力表|pressure gauge
dynamometer|弹簧测力计|spring balance|测力计
compass|指南针|compass|磁针
ruler|刻度尺|ruler|卷尺
protractor|量角器|protractor
vernier|游标卡尺|vernier caliper
micrometer|螺旋测微器|micrometer
dial_gauge|百分表|dial gauge
stopwatch|秒表|stopwatch
ticker_timer|打点计时器|ticker timer|纸带
photogate|光电门|photogate
balance|托盘天平|balance|天平
electronic_balance|电子天平|electronic balance|电子秤
hotplate|电热板|hotplate
stirrer|磁力搅拌器|magnetic stirrer
water_bath|水浴锅|water bath|油浴
heating_mantle|加热套|heating mantle
ph_meter|pH计|pH meter
conductivity_meter|电导率仪|conductivity meter
oscilloscope|示波器|oscilloscope
signal_generator|信号发生器|signal generator
power_supply|学生电源|power supply
spectrophotometer|分光光度计|spectrophotometer
centrifuge|离心机|centrifuge
drying_oven|干燥箱|drying oven
""",
"waves": """
transverse|横波|transverse wave|绳波
standing_wave|驻波|standing wave
longitudinal|纵波|longitudinal wave|弹簧纵波
wavefront|波前|wavefront
wave_source|波源|wave source
tuning_fork|音叉|tuning fork
speaker|扬声器|speaker
resonance_tube|共鸣管|resonance tube
air_column|空气柱|air column
water_wave_tank|水波槽|ripple tank
vibrating_membrane|振动膜|vibrating membrane
magnet|条形磁铁|bar magnet
horseshoe_magnet|蹄形磁铁|horseshoe magnet
electromagnet|电磁铁|electromagnet
field_lines|磁感线|magnetic field lines
into_page|进纸面符号|into page
out_of_page|出纸面符号|out of page
syringe|注射器|syringe
piston|气缸活塞|piston
hydraulic_press|液压装置|hydraulic press
manometer|压强计|manometer
communicating_vessels|连通器|communicating vessels
gas_container|气体分子容器|gas container
particle_container|微粒容器|particle container
""",
"biology": """
animal_cell|动物细胞|animal cell
plant_cell|植物细胞|plant cell
bacterium|原核细胞|bacterium
virus|病毒|virus
membrane|细胞膜|membrane
cell_wall|细胞壁|cell wall
nucleus|细胞核|cell nucleus
vacuole|液泡|vacuole
mitochondrion|线粒体|mitochondrion
chloroplast|叶绿体|chloroplast
ribosome|核糖体|ribosome
er|内质网|endoplasmic reticulum
golgi|高尔基体|Golgi apparatus
red_cell|红细胞|red blood cell
white_cell|白细胞|white blood cell
neuron|神经元|neuron
muscle|肌纤维|muscle fiber
stomata|气孔|stomata
leaf|叶片|leaf
leaf_section|叶横切面|leaf section
root|根|root
stem|茎|stem
flower|花|flower
seed|种子|seed
root_tip|根尖|root tip
heart|心脏|heart
lungs|肺|lungs
alveoli|肺泡|alveoli
digestion|消化道|digestive system
kidney|肾|kidney
nephron|肾单位|nephron
reflex|反射弧结构|reflex structure
ear|耳|ear
joint|骨与关节|joint
dna|DNA双链|DNA
rna|RNA|RNA
base_pair|碱基配对|base pair
chromosome|染色体|chromosome
homologous|同源染色体|homologous chromosomes
chromatids|姐妹染色单体|sister chromatids
mitosis|有丝分裂|mitosis
meiosis|减数分裂|meiosis
replication|DNA复制|DNA replication
transcription|转录|transcription
translation|翻译|translation
microscope|显微镜|microscope
slide|载玻片|microscope slide
coverslip|盖玻片|coverslip
inoculation_loop|接种环|inoculation loop
dialysis_bag|透析袋|dialysis bag
respirometer|呼吸计|respirometer
potometer|蒸腾计|potometer
quadrat|样方框|quadrat
culture_flask|培养瓶|culture flask
germination|种子萌发|germination
phototropism|向光性|phototropism
osmosis|渗透|osmosis
plasmolysis|质壁分离|plasmolysis
enzyme|酶作用|enzyme
""",
"earth": """
sun|太阳|sun
earth|地球|earth
moon|月球|moon
globe|地球仪|globe|经纬网
latitude|纬线|latitude
longitude|经线|longitude
day_night|昼夜界线|day night
earth_layers|地球内部圈层|earth layers
orbit|轨道|orbit
seasons|四季位置关系|seasons
moon_phase|月相|moon phase
eclipse|日食|solar eclipse
lunar_eclipse|月食|lunar eclipse
mountain|山地|mountain
valley|山谷|valley
ridge|山脊|ridge
saddle|鞍部|saddle
contour|等高线|contours
terrain_profile|地形剖面|terrain profile
river|河流|river
watershed|流域|watershed
volcano|火山|volcano
strata|岩层|strata
fold|褶皱|fold
fault|断层|fault
plate_boundary|板块边界|plate boundary
subduction|俯冲|subduction
coast|海岸剖面|coast
cloud|云|cloud
rain|降水|rain
evaporation|蒸发|evaporation
condensation|凝结|condensation
runoff|地表径流|runoff
groundwater|地下水|groundwater
convection|对流|convection
sea_breeze|海陆风|sea breeze
valley_breeze|山谷风|valley breeze
cold_front|冷锋|cold front
warm_front|暖锋|warm front
wind|风向|wind
water_cycle|水循环|water cycle
north_arrow|指北针|north arrow
scale_bar|比例尺|scale bar
map_pin|地点标记|map pin
route|路线|route
region|区域填充|region
migration|迁移箭头|migration
""",
"chemistry": """
atom|原子|atom
nucleus|原子核|atomic nucleus
proton|质子|proton
neutron|中子|neutron
electron|电子|electron
ion|离子|ion
electron_pair|电子对|electron pair
isotope|同位素示意|isotope
single_bond|单键|single bond
double_bond|双键|double bond
triple_bond|三键|triple bond
wedge|楔形键|wedge bond
hashed_wedge|虚楔键|hashed wedge
chain|碳链|carbon chain
branched_chain|支链|branched chain
benzene|苯环|benzene ring
ring|环结构|ring structure
chair|椅式构象|chair conformation
hydroxyl|羟基|hydroxyl
aldehyde|醛基|aldehyde
carboxyl|羧基|carboxyl
ester|酯基|ester
amino|氨基|amino
polymer|聚合物重复单元|polymer
h2|氢气分子|hydrogen molecule
o2|氧气分子|oxygen molecule
n2|氮气分子|nitrogen molecule
water|水分子|water molecule
co2|二氧化碳分子|carbon dioxide
ammonia|氨分子|ammonia
methane|甲烷分子|methane
hcl|氯化氢分子|hydrogen chloride
ethanol|乙醇分子|ethanol
acetic_acid|乙酸分子|acetic acid
ethylene|乙烯分子|ethylene
acetylene|乙炔分子|acetylene
pure|纯净物微观图|pure substance
mixture|混合物微观图|mixture
solution|离子溶液|ionic solution
gas_mixture|气体混合物|gas mixture
precipitate|沉淀|precipitate
crystal|晶体|crystal
powder|粉末|powder
suspension|悬浊液|suspension
emulsion|乳浊液|emulsion
nacl|氯化钠晶格|NaCl lattice
diamond|金刚石结构|diamond lattice
graphite|石墨层状结构|graphite layers
metal_lattice|金属晶格|metal lattice
bubbles|气泡|bubbles
drop|液滴|drop
flame|火焰|flame
diffusion|扩散|diffusion
""",
"graph": """
array|数组|array
stack|栈|stack
queue|队列|queue
circular_queue|循环队列|circular queue
matrix|矩阵|matrix|增广矩阵
table|列联表|contingency table|关系表
payoff|收益矩阵|payoff matrix
place_value|位值表|place value table
comparison|对照图|comparison|实验变量对照
venn2|两集合韦恩图|Venn diagram
venn3|三集合韦恩图|three set Venn
euler|欧拉图|Euler diagram
tree|树|tree
binary_tree|二叉树|binary tree|搜索树
heap|堆|heap
probability_tree|概率树|probability tree
syntax_tree|句法树|syntax tree
organization|组织结构图|organization chart
pedigree|遗传系谱|pedigree
argument|论证结构图|argument map
paragraph|段落关系图|paragraph structure
ecosystem|生态金字塔|ecological pyramid
timeline|事件时间轴|timeline|历史时间轴
cashflow|现金流时间轴|cash flow
gantt|甘特图|Gantt chart
sentence|句子成分框|sentence constituents
morpheme|词形构成图|morphology
story|故事结构图|story structure
undirected|无向图|undirected graph
directed|有向图|directed graph|食物网
bipartite|二分图|bipartite graph
hasse|哈斯图|Hasse diagram
flow|流程图|flowchart
state_machine|状态机|state machine
dataflow|数据流图|data flow
class|类关系框|class diagram
er|实体关系图|entity relationship
linked_list|链表|linked list
double_list|双向链表|doubly linked list
network|网络连接|network
process|处理框|process
""",
"logic": """
and|与门|AND gate
or|或门|OR gate
not|非门|NOT gate
xor|异或门|XOR gate
nand|与非门|NAND gate
nor|或非门|NOR gate
xnor|同或门|XNOR gate
flip_flop|触发器|flip flop
register|寄存器|register
mux|多路选择器|multiplexer
decoder|译码器|decoder
""",
"objects": """
person|人物简图|person
hand|手部示意|hand
apple|苹果|apple
orange|橙子|orange
tree|树木|tree object
balloon|气球|balloon
car|汽车|car
bus|公交车|bus
truck|卡车|truck
train|火车|train
bicycle|自行车|bicycle
boat|船|boat
book|书|book
pen|笔|pen
backpack|书包|backpack
box|箱|box
basket|篮|basket
bag|袋|bag|抽球袋
house|房屋|house
stairs|楼梯|stairs
bridge|桥|bridge
pool|水池|pool
tap|水龙头|tap
ticket|票据|ticket
paper|纸|paper
server|服务器|server
computer|计算机|computer
router|路由器|router
network_switch|交换机|network switch
database|数据库|database
gear|齿轮|gear
gears|齿轮组|gears
bearing|轴承|bearing
beam|梁与支座|beam
valve|阀门|valve
pump|泵|pump
heat_exchanger|换热器|heat exchanger
turbine|涡轮|turbine
tank|储罐|storage tank
""",
"template": """
heating_beaker|烧杯间接加热|beaker heating|加热烧杯
heating_tube|试管加热|test tube heating
filtration|过滤装置|filtration
evaporation|蒸发装置|evaporation setup
crystallization|结晶实验|crystallization
distillation|蒸馏装置|distillation
separation|分液装置|separation setup
extraction|萃取实验|extraction
titration|滴定装置|titration
gas_wash|气体洗涤|gas washing
gas_dry|气体干燥|gas drying
gas_water|排水集气|water displacement
gas_up|向上排空气集气|upward gas collection
gas_down|向下排空气集气|downward gas collection
gas_preparation|气体制备装置|gas preparation
galvanic_cell|原电池装置|galvanic cell
electrolysis|电解池装置|electrolysis
chromatography|纸色谱|paper chromatography
flame_test|焰色实验|flame test
horizontal_block|水平面滑块|horizontal block scene
inclined_block|斜面滑块|inclined block scene
pulley_blocks|两物体经滑轮连接|pulley scene|滑轮组
spring_horizontal|水平弹簧振子装置|spring mass
spring_vertical|竖直弹簧振子装置|vertical spring mass
pendulum|单摆装置|pendulum scene
lever_balance|杠杆平衡|lever balance
collision|碰撞实验|collision
projectile|平抛情境|projectile
circular_motion|圆周运动情境|circular motion
buoyancy|浮力实验|buoyancy
overflow|溢水杯实验|overflow experiment
manometer|U形管压强实验|pressure experiment
communicating|连通器实验|communicating experiment
hydraulic|液压实验|hydraulic experiment
piston|气缸活塞实验|piston scene
thermal|物体受热|heating experiment
conduction|热传导演示|heat conduction
series_circuit|基本串联电路|series circuit
parallel_circuit|基本并联电路|parallel circuit
resistance|伏安法测电阻|resistance measurement
lamp_power|小灯泡功率测量|lamp power
rheostat_wiring|滑动变阻器接线|rheostat wiring
induction|电磁感应导轨|induction rails
transformer|变压器示意|transformer scene
mirror|平面镜反射|mirror reflection
refraction|玻璃砖折射|refraction
lens_bench|凸透镜成像装置|lens imaging
concave_lens|凹透镜光路|concave lens scene
pinhole|小孔成像装置|pinhole imaging
prism|三棱镜光路|prism scene
double_slit|双缝装置|double slit experiment
microscopy|显微镜观察|microscopy
germination|种子萌发对照|germination comparison
respiration|呼吸实验|respiration experiment
transpiration|蒸腾实验|transpiration
osmosis|渗透与透析|osmosis experiment
plasmolysis|质壁分离实验|plasmolysis experiment
enzyme|酶作用对照|enzyme comparison
pedigree|遗传系谱分析|pedigree scene
seasons|地球公转季节|seasons scene
water_cycle|水循环过程|water cycle scene
food_web|食物网关系|food web
supply_demand|供需曲线|supply demand
binary_tree|二叉树遍历|binary tree traversal
flowchart|流程判断|flowchart scene
experiment_control|实验变量对照图|control experiment
probability|概率树与结果|probability scene
triangle_angles|三角形角度关系|triangle angles
inscribed_triangle|圆内接三角形|inscribed triangle
tangent_radius|切线与半径|tangent radius
two_circles|两圆位置关系|two circles
similar_triangles|相似三角形|similar triangles
solid_section|立体截面|solid section scene
solid_net|几何体展开|solid net scene
function_tangent|函数切线构型|function tangent scene
integral|积分区域构型|integral scene
bar_table|柱状图构型|bar chart scene
pie_table|饼图构型|pie chart scene
box_comparison|箱线图比较|box comparison
scatter_fit|散点图与拟合|scatter scene
histogram|分组直方图|histogram scene
""",
}


# These previously deferred IDs now have dedicated registered implementations.
RESTORED = {
    "biology": {"root_tip"},
    "circuit": {"mosfet"},
    "earth": {"moon_phase", "plate_boundary", "subduction", "sea_breeze", "valley_breeze", "wind", "watershed"},
    "chemistry": {"isotope", "electron_pair", "ion", "ester", "solution", "precipitate", "suspension", "emulsion", "diamond", "graphite", "diffusion"},
    "graph": {"cashflow", "gantt", "pedigree", "ecosystem", "hasse", "bipartite", "er", "class", "double_list"},
    "logic": {"decoder", "register"},
    "objects": {"heat_exchanger", "truck", "train"},
    "template": {"distillation", "gas_water", "gas_up", "gas_down", "gas_preparation", "gas_wash", "gas_dry",
                 "galvanic_cell", "electrolysis", "chromatography", "flame_test", "extraction", "crystallization",
                 "manometer", "thermal", "conduction", "rheostat_wiring", "induction", "transformer", "lens_bench",
                 "concave_lens", "pinhole", "prism", "double_slit", "germination", "respiration", "transpiration", "enzyme", "pedigree",
                 "experiment_control", "box_comparison", "scatter_fit"},
}


def build():
    from app.diagrams.extended import registrations, restored_sample, sample_params
    from app.diagrams.extended_inventory import entries
    from app.diagrams.curriculum_expansion import entries as expansion_entries
    from app.diagrams.provenance import enrich
    registered = registrations()
    assets = []
    for renderer, rows in INVENTORY.items():
        category = ("mathematics" if renderer in {"geometry", "function"} else "statistics" if renderer == "chart"
                    else "chemistry" if renderer in {"vessel", "apparatus", "chemistry"} else "physics" if renderer in {"mechanics", "circuit", "optics", "measurement", "waves"}
                    else "biology" if renderer == "biology" else "geography" if renderer == "earth" else "systems")
        for row in rows.strip().splitlines():
            values = row.split("|")
            variant, title, english = values[:3]
            asset_category = category
            if renderer == "template":
                if variant in {"heating_beaker", "heating_tube", "filtration", "evaporation", "separation", "titration"}:
                    asset_category = "chemistry"
                elif variant in {"microscopy", "osmosis", "plasmolysis"}:
                    asset_category = "biology"
                elif variant in {"seasons", "water_cycle"}:
                    asset_category = "geography"
                elif variant in {"triangle_angles", "inscribed_triangle", "tangent_radius", "two_circles", "similar_triangles", "solid_section", "solid_net", "function_tangent", "integral"}:
                    asset_category = "mathematics"
                elif variant in {"bar_table", "pie_table", "histogram"}:
                    asset_category = "statistics"
                elif variant not in {"food_web", "supply_demand", "binary_tree", "flowchart", "probability"}:
                    asset_category = "physics"
            aliases = values[3].split(",") if len(values) > 3 else []
            features = []
            if renderer == "vessel":
                if variant not in {"drying_tube", "bell_jar", "desiccator", "crucible", "evaporating_dish", "mortar", "watch_glass", "petri_dish"}:
                    features += ["可显示液面", "盛液"]
                if variant in {"test_tube", "hard_test_tube", "sidearm_test_tube", "conical_flask", "round_flask", "flat_flask", "distilling_flask", "three_neck_flask", "gas_wash_bottle", "wash_bottle", "drying_tube"}:
                    features += ["接导管"]
                if variant in {"beaker", "tall_beaker", "test_tube", "hard_test_tube", "conical_flask", "evaporating_dish", "crucible"}:
                    features += ["敞口", "可加热"]
                if variant in {"sidearm_test_tube", "distilling_flask"}:
                    features += ["带侧管"]
                if variant in {"cylinder", "measuring_cup", "beaker"}:
                    features += ["刻度"]
            if renderer == "apparatus" and variant in {"alcohol_lamp", "bunsen_burner"}:
                features += ["点燃状态", "可加热"]
            if renderer == "apparatus" and variant in {"tube", "bent_tube", "u_tube", "tee_tube", "hose", "condenser", "allihn_condenser", "graham_condenser", "air_condenser", "drying_tube"}:
                features += ["接导管"]
            if renderer == "mechanics" and variant in {"block", "metal_block", "cart", "collision_cart", "ball", "hollow_ball", "disc", "ring", "pendulum_bob", "spring", "vertical_spring", "spring_oscillator", "vertical_oscillator", "pendulum", "physical_pendulum", "double_pendulum", "pulley", "moving_pulley", "wheel_axle", "weight", "hook"}:
                features += ["接绳"]
            if renderer in {"circuit", "logic"} or renderer == "measurement" and variant in {"meter", "ammeter_real", "voltmeter_real", "multimeter", "power_supply", "oscilloscope", "signal_generator"}:
                features += ["电接线"]
            if renderer == "measurement" and variant in {"meter", "ammeter_real", "voltmeter_real", "multimeter", "pressure_gauge", "dynamometer", "ruler", "protractor", "vernier", "micrometer", "dial_gauge", "stopwatch"} or variant in {"burette", "base_burette", "thermometer", "graduated_pipette"}:
                features += ["刻度"]
            sample = {}
            if renderer == "vessel" and variant not in {"watch_glass", "petri_dish", "mortar", "crucible", "evaporating_dish", "bell_jar", "desiccator", "drying_tube"}:
                sample = {"fill": .45}
            if renderer == "apparatus" and variant in {"alcohol_lamp", "bunsen_burner"}:
                sample = {"lit": True}
            if renderer == "template" and variant in {"heating_beaker", "heating_tube", "evaporation"}:
                sample = {"lit": True, **({"fill": .35} if variant != "evaporation" else {})}
            if renderer == "chart" or renderer == "template" and variant in {"bar_table", "pie_table", "box_comparison", "scatter_fit", "histogram"}:
                sample = {"values": [30, 45, 25], "labels": ["A", "B", "C"]}
                if variant == "histogram":
                    sample["bin_edges"] = [0, 1, 2, 3]
                if variant in {"grouped", "stacked", "percent_stacked", "multi_line", "stacked_area", "heatmap"}:
                    sample["values"] = [[12, 24, 18], [9, 15, 20]]
                if variant == "population":
                    sample["values"] = [[12, 24, 18], [9, 15, 20]]
                if variant == "funnel":
                    sample["values"] = [90, 60, 30]
                if variant == "pictograph":
                    sample["values"] = [5, 8, 3]
                if variant == "bubble":
                    sample["points"] = [[1, 30, 2], [2, 45, 12], [3, 25, 5]]
                if variant in {"errorbar", "confidence"}:
                    sample.update({"values": [20, 30, 40], "errors": [3, 4, 3]})
                if variant in {"box", "density", "qq", "dot", "stem_leaf", "normal", "t_distribution", "ecdf", "box_comparison"}:
                    sample = {"values": [12, 14, 18, 19, 22, 24, 28, 32]}
            if renderer == "graph" or renderer == "template" and variant in {"pedigree", "food_web", "binary_tree", "flowchart", "experiment_control", "probability"}:
                sample = {"items": ["A", "B", "C", "D", "E", "F", "G"]}
                if variant in {"venn2", "euler"}:
                    sample["items"] = ["A", "B"]
                if variant == "venn3":
                    sample["items"] = ["A", "B", "C"]
            assets.append({"id": f"{renderer}.{variant}", "title": title, "english": english,
                           "category": asset_category, "renderer": renderer, "variant": variant,
                           "version": 2 if f"{renderer}.{variant}" in {"chemistry.water", "biology.chloroplast"} else 1,
                           "aliases": aliases, "features": features,
                           "sample_params": sample, "license": "original-project-artwork"})
            if variant in RESTORED.get(renderer, set()):
                if f"{renderer}.{variant}" not in registered:
                    raise ValueError(f"missing restored renderer: {renderer}.{variant}")
                assets[-1]["sample_params"] = restored_sample(renderer, variant)
                if renderer == "template":
                    if variant in {"box_comparison", "scatter_fit"}: assets[-1]["category"] = "statistics"
                    elif variant in {"germination", "respiration", "transpiration", "enzyme", "pedigree", "experiment_control"}: assets[-1]["category"] = "biology"
                    elif variant in {"distillation", "gas_water", "gas_up", "gas_down", "gas_preparation", "gas_wash", "gas_dry", "galvanic_cell", "electrolysis", "chromatography", "flame_test", "extraction", "crystallization"}: assets[-1]["category"] = "chemistry"
    expansion = expansion_entries()
    expansion_ids = {row["id"] for row in expansion}
    for asset in list(entries()) + expansion:
        if asset["id"] not in expansion_ids:
            asset["sample_params"] = sample_params(asset["category"], asset["variant"])
        schema = registered[asset["id"]].parameters(asset["variant"])
        asset["features"] = ["数据驱动"] if any(spec.get("required") for spec in schema.values()) else []
        if asset["variant"] in {"vernier_reading", "micrometer_reading", "scope_reading"}:
            asset["features"].append("刻度")
        assets.append(asset)
    return {"version": "1.1.0", "assets": [enrich(asset) for asset in assets]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "assets" / "diagram_library" / "catalog.json"
    data = build()
    contents = {output: json.dumps(data, ensure_ascii=False, indent=2)+"\n",
                ROOT.parents[1] / "docs" / "reference" / "diagram-assets.md": inventory_markdown(data)}
    for path, content in contents.items():
        if args.check:
            if not path.exists() or path.read_text("utf-8") != content:
                raise SystemExit(f"diagram resource is out of date: {path.name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, "utf-8")
    print(f"{len(data['assets'])} runnable diagram assets")


def inventory_markdown(data):
    from app.diagrams.taxonomy import SUBJECTS, FAMILIES
    subjects = {key: labels[0] for key, labels in SUBJECTS.items()}
    families = {key: labels[0] for key, labels in FAMILIES.items()}
    passed = sum(a["review"]["status"] == "passed" for a in data["assets"])
    lines = ["# 教学 SVG 素材完整清单", "", "<!-- Generated by services/api/scripts/build_diagram_catalog.py; do not edit manually. -->", "",
             f"目录版本 `{data['version']}`，共 **{len(data['assets'])}** 个已实现素材，**{passed}** 个通过审核。全部由项目矢量代码绘制，通过审核者可在前端 `/diagram-library` 查看。", "",
             "参数范围、原生尺寸和锚点以素材详情接口为准；预览数据仅作展示。架构、接入与验收方法见 [architecture/diagrams-illustration.md](../architecture/diagrams-illustration.md)。", "",
             "| 学科 | 正式素材数 |", "| --- | ---: |"]
    counts = Counter(a["category"] for a in data["assets"])
    lines.extend(f"| {name} | {counts[key]} |" for key, name in subjects.items())
    for family, name in families.items():
        assets = [a for a in data["assets"] if a["renderer"] == family]
        lines.extend(["", f"## {name}（{len(assets)}）", "", "| 素材 ID | 名称 | 英文 | 别名 | 功能 |", "| --- | --- | --- | --- | --- |"])
        for asset in assets:
            lines.append(f"| `{asset['id']}` | {asset['title']} | {asset['english']} | {'、'.join(asset['aliases']) or '—'} | {'、'.join(asset['features']) or '固定结构 / 数据驱动'} |")
    lines.extend(["", "## 来源与验收", "", "每个素材的来源模块、源码哈希、事实参考与检查状态均保存在版本化目录中。图形未使用第三方素材。审核记录区分智能体和人工检查；未通过者不参与公开检索。"])
    return "\n".join(lines)+"\n"


if __name__ == "__main__":
    main()
