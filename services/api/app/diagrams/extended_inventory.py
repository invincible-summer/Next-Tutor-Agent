"""400 independent teaching compositions, with stable original-project IDs."""

INVENTORY = {
    "mathematics": """
bisector_construction|角平分线作图过程|Angle bisector construction
perpendicular_bisector|垂直平分线作图|Perpendicular bisector construction
alternate_angles|平行线内错角|Alternate interior angles
corresponding_angles|同位角|Corresponding angles
power_circle|圆幂关系|Power of a point
intersecting_chords|相交弦关系|Intersecting chords
common_tangents|两圆公切线|Common circle tangents
pythagorean_puzzle|勾股面积拼图|Pythagorean area dissection
similarity_scale|相似比例尺构型|Similarity and scale
centroid|三角形重心构型|Triangle centroid
orthocenter|垂心构型|Triangle orthocenter
nine_point_circle|九点圆|Nine-point circle
ellipse_foci|椭圆焦点构型|Ellipse foci
hyperbola_asymptotes|双曲线渐近线构型|Hyperbola asymptotes
parabola_directrix|抛物线焦点准线构型|Parabola focus and directrix
polar_curve|极坐标曲线|Polar curve
parametric_curve|参数曲线|Parametric curve
piecewise_function|分段函数|Piecewise function
inverse_symmetry|反函数对称|Inverse function symmetry
inequality_line|不等式解集数轴|Inequality number line
feasible_region|二维可行域|Two-dimensional feasible region
space_axes|空间坐标系|Spatial coordinate axes
space_vector|空间向量|Spatial vector
plane_normal|平面法向量|Plane normal vector
solid_revolution|旋转体剖面|Solid of revolution section
cone_net|圆锥展开|Cone net
cylinder_net|圆柱展开|Cylinder net
pyramid_net|正棱锥展开|Regular pyramid net
frustum_net|截锥展开|Frustum net
euler_polyhedron|多面体欧拉关系|Polyhedron Euler relation
tangram|七巧板拼图|Tangram
area_dissection|面积割补|Area dissection
unit_cubes|体积单位拼装|Unit-cube volume model
equation_balance|算式天平|Equation balance
grouped_array|乘法分组阵列|Grouped multiplication array
equivalent_fractions|分数等值拼图|Equivalent fraction composition
""",
    "physics": """
free_body|自由体受力图|Free-body diagram
oblique_projectile|斜抛轨迹|Oblique projectile
rope_constraint|绳约束运动|Rope-constrained motion
rod_constraint|杆约束运动|Rod-constrained motion
binary_orbit|双星绕质心|Binary barycentric orbit
centripetal_components|向心加速度分解|Centripetal acceleration components
newton_cradle|牛顿摆|Newton cradle
center_support|刚体重心支撑|Center-of-mass support
inertia_comparison|转动惯量对照|Moment of inertia comparison
angular_momentum|角动量转盘|Angular momentum platform
pv_cycle|P-V循环|Pressure-volume cycle
heat_engine|热机能流|Heat engine energy flow
refrigeration|制冷循环|Refrigeration cycle
phase_diagram|固液气相图|Solid-liquid-gas phase diagram
speed_distribution|分子速率分布|Molecular speed distribution
point_charge|点电荷场线|Point charge field
dipole_field|偶极场线|Dipole field
plate_field|平行板电场|Parallel-plate field
equipotential|等势线|Equipotential contours
charged_trajectory|带电粒子轨迹|Charged-particle trajectory
cyclotron|回旋加速器|Cyclotron
hall_element|霍尔元件|Hall element
capacitor_rc|电容充放电|Capacitor charging and discharging
ac_phasor|交流相量|AC phasor
lc_oscillation|LC振荡|LC oscillation
standing_nodes|驻波节点腹点|Standing-wave nodes and antinodes
doppler|多普勒波前|Doppler wavefronts
superposition|波叠加|Wave superposition
beats|声波拍频|Acoustic beats
interference_fringes|干涉条纹|Interference fringes
single_diffraction|单缝衍射|Single-slit diffraction
polarizers|偏振片组|Polarizer pair
critical_angle|全反射临界角|Critical-angle construction
fiber_reflection|光纤全反射|Optical fiber total reflection
michelson|迈克耳孙干涉仪|Michelson interferometer
vernier_reading|游标读数放大构图|Magnified vernier reading
micrometer_reading|螺旋测微器读数放大构图|Magnified micrometer reading
scope_reading|示波器读波形构图|Oscilloscope waveform reading
ultrasonic_distance|超声测距装置|Ultrasonic distance apparatus
fall_timing|落球计时装置|Falling-ball timing apparatus
""",
    "chemistry": """
tetrahedral|四面体分子构型|Tetrahedral geometry
trigonal_pyramidal|三角锥分子构型|Trigonal pyramidal geometry
linear_geometry|直线型分子构型|Linear molecular geometry
trigonal_planar|平面三角分子构型|Trigonal planar geometry
cis_trans|顺反异构|Cis-trans isomerism
chirality|手性中心|Chiral center
hydrogen_network|氢键网络|Hydrogen-bond network
hybrid_orbitals|杂化轨道方向示意|Hybrid orbital directions
vacuum_filtration|真空抽滤装置|Vacuum filtration
reflux|回流反应装置|Reflux apparatus
fractional_distillation|分馏装置|Fractional distillation
rotary_evaporator|旋转蒸发仪|Rotary evaporator
leak_check|气密性检查构图|Gas-tightness check
reaction_calorimeter|反应量热装置|Reaction calorimeter
gas_syringe|气体注射器采集|Gas syringe collection
precipitate_wash|洗涤沉淀构图|Precipitate washing
pipette_transfer|移液定容操作序列|Pipette and dilution sequence
standard_solution|标准溶液配制序列|Standard solution preparation
microplate|微量滴定板|Micro titration plate
tlc_chamber|薄层色谱展开槽|TLC developing chamber
titration_curve|酸碱滴定曲线|Acid-base titration curve
solubility_curve|溶解度曲线|Solubility curve
activation_energy|反应能垒与催化对照|Activation energy and catalysis
gas_burette|量气管读数构图|Gas burette reading
equilibrium_time|化学平衡浓度时间图|Equilibrium concentration-time plot
equilibrium_shift|化学平衡移动对照|Equilibrium shift comparison
orbital_boxes|电子排布轨道框|Orbital occupancy boxes
periodic_table|周期表结构索引|Periodic table structure
mass_spectrum|质谱棒图|Mass spectrum
ir_spectrum|红外吸收谱|Infrared absorption spectrum
nmr_spectrum|核磁共振峰图|NMR spectrum
molecular_vibration|分子振动模式|Molecular vibration modes
""",
    "biology": """
flower_section|花的纵剖面|Flower longitudinal section
dicot_seed|双子叶种子剖面|Dicot seed section
monocot_seed|单子叶种子剖面|Monocot seed section
xylem_phloem|木质部与韧皮部|Xylem and phloem
root_hair|根毛吸收|Root-hair uptake
leaf_exchange|叶肉气体交换|Leaf gas exchange
annual_rings|木本茎年轮|Woody stem annual rings
asexual_reproduction|无性繁殖对照|Asexual reproduction comparison
vessels_comparison|动脉静脉毛细血管|Artery vein and capillary
circulations|体肺循环|Pulmonary and systemic circulation
alveolar_exchange|呼吸气体交换|Alveolar gas exchange
eye_refraction|眼球屈光结构|Eye refraction structure
skin_layers|皮肤分层|Skin layers
antagonistic_muscles|骨骼肌拮抗|Antagonistic muscles
immune_cells|免疫细胞协作|Immune-cell interactions
synapse|神经突触|Neural synapse
crossing_over|染色体交叉互换|Chromosome crossing-over
gene_linkage|基因连锁图|Gene linkage map
mendel|孟德尔分离组合|Mendelian segregation
sex_linkage|伴性遗传构型|Sex-linked inheritance
pcr|PCR循环|PCR cycle
electrophoresis|电泳条带|Gel electrophoresis bands
restriction_sites|限制酶切位点|Restriction sites
plasmid|质粒载体|Plasmid vector
transect|群落样带调查|Community transect
mark_recapture|种群标志重捕|Mark and recapture
serial_dilution|微生物稀释涂布|Serial dilution and plating
niche_resources|生态位资源分布|Resource niche distributions
""",
    "geography": """
meander|河曲侵蚀堆积|Meander erosion and deposition
delta|三角洲|River delta
alluvial_fan|冲积扇|Alluvial fan
karst|喀斯特剖面|Karst section
glacier|冰川地貌|Glacial landforms
isobar_wind|等压线风场|Isobars and wind
pressure_belts|气压带风带|Pressure and wind belts
monsoon|季风环流|Monsoon circulation
front_section|锋面剖面|Weather front section
orographic_rain|地形雨|Orographic rainfall
climate_plot|气温降水组合|Temperature and precipitation
earthquake|地震震源震中|Hypocenter and epicenter
travel_time|走时曲线|Seismic travel-time curves
rock_cycle|岩石循环|Rock cycle
ocean_gyres|洋流环流|Ocean gyres
time_zones|时区计算构型|Time-zone construction
solar_altitude|太阳高度测量|Solar altitude measurement
projection_distortion|地球投影变形对照|Map projection distortion
urban_zones|城市功能分区|Urban functional zones
migration_network|人口迁移网络|Population migration network
""",
    "statistics": """
violin|小提琴图|Violin plot
beeswarm|蜂群图|Beeswarm plot
raincloud|雨云图|Raincloud plot
mosaic|马赛克图|Mosaic plot
residuals|残差图|Residual plot
prediction_interval|回归预测区间|Regression prediction interval
agreement|一致性比较图|Agreement plot
lorenz|洛伦兹曲线|Lorenz curve
pareto|帕累托图|Pareto chart
sankey|桑基流量图|Sankey flow diagram
treemap|树状矩形图|Treemap
slope|斜率图|Slope chart
bullet|子弹图|Bullet chart
kde_contours|二维核密度等高图|Bivariate kernel-density contours
binomial|二项分布|Binomial distribution
poisson|泊松分布|Poisson distribution
chi_square|卡方分布|Chi-square distribution
f_distribution|F分布|F distribution
sampling_distribution|抽样分布与总体比较|Population and sampling distribution
permutation_distribution|置换分布|Permutation distribution
""",
    "systems": """
adjacency|图的邻接矩阵对照|Graph and adjacency matrix
dfs|DFS过程|Depth-first search sequence
bfs|BFS过程|Breadth-first search sequence
shortest_path|最短路径演示|Shortest path construction
topological|拓扑排序过程|Topological sorting
spanning_tree|最小生成树过程|Minimum spanning tree
hash_chain|哈希冲突链|Hash chaining
open_addressing|开放寻址|Open addressing
b_tree|B树结构|B-tree
red_black|红黑树结构|Red-black tree
union_find|并查集|Disjoint sets
trie|字典树|Trie
cpu_cycle|CPU取指译码执行|Fetch decode execute cycle
memory_hierarchy|内存层级|Memory hierarchy
paging|分页地址转换|Paged address translation
round_robin|进程调度时间片|Round-robin scheduling
encapsulation|网络分层封装|Network encapsulation
packet_switching|数据包交换|Packet switching
tcp_sequence|TCP时序图|TCP sequence diagram
regex_automaton|正则表达式与自动机对照|Regular expression and automaton
neural_network|神经网络层连接|Neural network layers
convolution|卷积滑窗|Convolution sliding window
decision_split|决策树分裂|Decision-tree split
confusion_matrix|混淆矩阵|Confusion matrix
""",
    "language": """
tian_grid|田字格|Four-square writing grid
mi_grid|米字格|Eight-direction writing grid
palace_grid|回宫格|Nested writing grid
handwriting_lines|四线三格|Four-line handwriting grid
stroke_grid|汉字笔顺网格|Stroke-order writing panels
radical_assembly|偏旁部件拼合|Radical assembly
character_structure|汉字结构对照|Character structure comparison
pinyin_syllable|拼音音节拼合|Pinyin syllable assembly
tone_contours|声调轨迹|Tone contours
english_syllables|英语音节划分|English syllable division
affixes|单词词缀分解|Affix structure
dependency|句子成分依存图|Sentence dependencies
agreement|主谓一致对应|Subject-verb agreement
tense_timeline|时态时间关系|Tense timeline
subordinate_clauses|从句嵌套|Clause nesting
paragraph_support|段落主题支撑|Topic and supporting sentences
general_specific|总分结构|General and specific structure
narrative_view|叙事视角|Narrative perspectives
dialogue_turns|对话轮次|Dialogue turns
rhetorical_relations|修辞关系比较|Rhetorical relationships
""",
    "history": """
chronology|纪年换算|Era conversion
parallel_civilizations|平行文明时间带|Parallel civilization timeline
causality|历史因果链|Historical causal chain
evidence_compare|史料证据对照|Historical evidence comparison
settlement|聚落布局|Settlement layout
city_gate|城墙城门|City wall and gate
arch_bridge|拱券桥|Arch bridge
irrigation_channels|水利渠道|Irrigation channels
plough|农耕犁|Agricultural plough
waterwheel|水车|Waterwheel
spinning_wheel|手摇纺车|Spinning wheel
loom|织布机|Loom
pottery_wheel|制陶轮|Pottery wheel
pottery_forms|陶罐器型对照|Pottery forms
bronze_vessel|青铜容器通用结构|Generic bronze vessel
trade_routes|帆船商路构图|Sailing trade routes
movable_type|活字印刷工序|Movable-type printing
steam_factory|蒸汽动力工厂|Steam-powered factory
archaeology|考古地层|Archaeological layers
artifact_views|器物三视图|Artifact orthographic views
""",
    "economics": """
ppf|生产可能性边界|Production possibilities frontier
opportunity_cost|机会成本对照|Opportunity cost comparison
budget_line|预算线|Budget line
indifference|无差异曲线|Indifference curves
consumer_optimum|消费者最优|Consumer optimum
utility|总边际效用|Total and marginal utility
cost_curves|成本曲线组|Cost curves
profit|收益利润关系|Revenue and profit
tax_wedge|税收楔子|Tax wedge
price_controls|价格上下限|Price controls
surplus|消费者生产者剩余|Consumer and producer surplus
externality|外部性示意|Externalities
circular_flow|经济循环流|Circular economic flow
input_output|投入产出关系|Input-output relations
break_even|盈亏平衡|Break-even point
compound_growth|复利增长|Compound growth
annuity|年金现金流|Annuity cash flows
balance_sheet|资产负债结构|Balance-sheet structure
dupont|杜邦分析|DuPont decomposition
inventory_model|库存订货模型|Inventory ordering model
""",
    "engineering": """
rack_pinion|齿轮齿条|Rack and pinion
worm_gear|蜗轮蜗杆|Worm gear
belt_drive|带传动|Belt drive
chain_drive|链传动|Chain drive
crank_slider|曲柄滑块|Crank-slider mechanism
four_bar|四杆机构|Four-bar linkage
cam_follower|凸轮从动件|Cam and follower
bearing_section|轴承剖面|Bearing section
thread_joint|螺纹连接|Threaded joint
coupling|联轴器|Shaft coupling
truss_joint|桁架节点|Truss joint
cantilever|悬臂梁载荷|Cantilever load
supported_beam|简支梁载荷|Simply-supported beam load
axial_load|轴向拉压|Axial loading
torsion|扭转轴|Shaft torsion
hydraulic_valve|液压阀回路|Hydraulic valve circuit
pneumatic_control|气动控制回路|Pneumatic control circuit
feedback_control|传感器控制闭环|Sensor feedback loop
robot_chain|机械臂关节链|Robot joint chain
dimension_tolerance|工程尺寸公差|Engineering dimensions and tolerances
""",
    "astronomy": """
solar_system|太阳系轨道层次|Solar-system orbital hierarchy
planet_tilt|行星自转倾角|Planetary axial tilt
earth_moon_scale|地月尺度对照|Earth-Moon scale comparison
equatorial_coordinates|天球赤道坐标|Equatorial coordinates
horizontal_coordinates|地平坐标|Horizontal coordinates
stellar_parallax|恒星视差|Stellar parallax
annual_motion|周年视运动|Annual apparent motion
spectral_classes|恒星光谱分类|Stellar spectral classes
hr_diagram|赫罗图|Hertzsprung-Russell diagram
stellar_evolution|恒星演化路径|Stellar evolution
star_cluster|星团分布|Star cluster
spiral_galaxy|螺旋星系|Spiral galaxy
elliptical_galaxy|椭圆星系|Elliptical galaxy
refractor|透镜式望远镜光路|Refracting telescope rays
reflector|反射式望远镜光路|Reflecting telescope rays
space_telescope|空间望远镜结构|Space telescope structure
orbit_inclination|卫星轨道倾角|Orbital inclination
transfer_orbit|转移轨道|Transfer orbit
tidal_bulge|潮汐隆起|Tidal bulges
eclipse_detail|日地月食几何放大|Magnified eclipse geometry
""",
    "music": """
staff|五线谱|Staff
clef_position|谱号与谱线定位|Clef and staff positions
note_duration|音符时值|Note durations
rest_duration|休止符时值|Rest durations
time_signature|小节拍号|Time signature
beat_groups|节拍分组|Beat grouping
scale_keyboard|音阶键盘对应|Scale and keyboard
intervals|音程比较|Musical intervals
triads|三和弦|Triads
key_signature|调号排列|Key signatures
melody_contour|旋律轮廓|Melodic contour
rhythm_grid|节奏格|Rhythm grid
piano_keyboard|钢琴键盘|Piano keyboard
guitar_fretboard|吉他指板|Guitar fingerboard
string_instrument|弦乐发声结构|String instrument vibration
wind_column|管乐气柱结构|Wind instrument air column
drum_membrane|鼓膜振动|Drum membrane
instrument_families|乐器家族对照|Instrument families
musical_form|曲式结构|Musical form
textures|声部织体|Musical textures
""",
    "visual_art": """
hue_wheel|色相环|Hue wheel
warm_cool|冷暖色对照|Warm and cool colors
value_steps|明度阶梯|Value scale
chroma_steps|纯度阶梯|Chroma scale
complementary|互补色配色|Complementary colors
additive|加色混合|Additive color mixing
subtractive|减色混合|Subtractive color mixing
one_point|单点透视|One-point perspective
two_point|两点透视|Two-point perspective
three_point|三点透视|Three-point perspective
still_life|几何静物明暗|Geometric still life shading
cast_shadow|投影与光源|Light and cast shadow
thirds|构图三分线|Rule-of-thirds grid
visual_balance|视觉重心|Visual center of gravity
symmetry_balance|对称与均衡|Symmetry and balance
negative_space|正负形|Positive and negative space
repeating_pattern|纹样重复|Repeating motif
tessellation|二维镶嵌|Plane tessellation
paper_fold|纸模型折叠|Paper folding
layout_hierarchy|版面层级|Layout hierarchy
""",
    "sports": """
track|跑道几何|Running-track geometry
relay_zone|接力交接区域|Relay exchange zone
sprint_start|短跑起跑姿态|Sprint start posture
running_gait|跑步步态序列|Running gait sequence
long_jump|跳远动作阶段|Long-jump phases
vertical_jump|纵跳重心轨迹|Vertical-jump center of mass
throwing|投掷动作阶段|Throwing phases
basketball_court|篮球场教学示意|Basketball teaching court
football_pitch|足球场教学示意|Football teaching pitch
volleyball_court|排球场教学示意|Volleyball teaching court
badminton_court|羽毛球场教学示意|Badminton teaching court
table_tennis|乒乓球台|Table-tennis table
tennis_court|网球场教学示意|Tennis teaching court
gymnastics_balance|体操平衡支撑|Gymnastics balance
joint_angles|屈伸关节角度|Joint flexion and extension
stretch_posture|拉伸动作姿态|Stretching posture
swimming|游泳动作阶段|Swimming phases
heart_rate|心率运动时间图|Exercise heart-rate timeline
training_stations|训练站点循环|Training station circuit
landing_distribution|球类落点分布|Ball landing distribution
""",
    "agriculture": """
soil_profile|土壤剖面|Soil profile
soil_aggregate|土壤团粒|Soil aggregate
soil_triangle|土壤质地三角图|Soil texture triangle
root_depth|根系深度比较|Rooting depth comparison
plant_spacing|种植株行距|Row and plant spacing
rotation_layout|轮作布局|Crop rotation layout
intercropping|间作布局|Intercropping layout
seedling_tray|育苗盘|Seedling tray
transplant_rootball|移栽根团|Transplant root ball
greenhouse|温室结构|Greenhouse structure
drip_irrigation|滴灌系统|Drip irrigation
sprinkler|喷灌系统|Sprinkler irrigation
water_balance|灌溉水量平衡|Irrigation water balance
compost|堆肥分层|Compost layers
pollination|授粉过程|Pollination
grafting|嫁接接口|Graft union
cutting|扦插生根|Rooted cutting
nutrient_sites|植物营养缺素部位示意|Nutrient deficiency locations
crop_stages|农作物生长阶段|Crop growth stages
harvest_storage|收获储藏流程|Harvest and storage
""",
    "environment": """
carbon_cycle|碳循环|Carbon cycle
nitrogen_cycle|氮循环|Nitrogen cycle
phosphorus_cycle|磷循环|Phosphorus cycle
water_layers|水体分层|Water stratification
eutrophication|富营养化过程|Eutrophication
wastewater|污水处理流程|Wastewater treatment
drinking_water|饮用水净化流程|Drinking-water treatment
waste_sorting|垃圾分类流程|Waste sorting
recycling_loop|物质回收闭环|Material recycling loop
greenhouse_energy|温室效应能流|Greenhouse energy flows
urban_heat|城市热岛剖面|Urban heat-island section
rain_garden|雨水花园|Rain garden
sponge_city|海绵城市径流|Sponge-city runoff
ecological_corridor|生态廊道|Ecological corridor
fragmentation|栖息地破碎化|Habitat fragmentation
succession|生态恢复演替|Ecological succession
energy_conversion|能源转换对照|Energy conversion comparison
lifecycle_flow|生命周期物质流|Life-cycle material flow
air_dispersion|空气污染扩散|Air-pollutant dispersion
footprint|生态足迹构型|Ecological footprint composition
""",
}


def entries():
    for subject, rows in INVENTORY.items():
        for index, row in enumerate(rows.strip().splitlines()):
            variant, title, english = row.split("|")
            yield {
                "id": f"{subject}_extended.{variant}",
                "title": title,
                "english": english,
                "category": subject,
                "renderer": f"{subject}_extended",
                "variant": variant,
                "version": 1,
                "aliases": [],
                "features": [],
                "sample_params": {},
                "license": "original-project-artwork",
            }
