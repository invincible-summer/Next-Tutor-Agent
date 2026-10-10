"use client";

/** Domain re-exports for the workbench UI (keeps import lists short). */
export type {
  AnnotationDefinition, Camera3DSettings, DrawingMode, Domain2D, GeometryDefinition2D,
  MathCommand, MathWorkbenchDocument, NamedFunctionDefinition, ParameterDefinition, PlotDefinition2D, PlotDefinition3D,
  Pt2, Pt3, Render3DSettings, ResolvedGeometry, StyleSettings, SurfaceQuality, View2D,
  ResolvedGeometry2D, ResolvedCircle2D, ResolvedArc2D, ResolvedPoint2D, ResolvedLine2D, ResolvedPolygon2D,
  EvaluationContext, UserFunctionEntry, CompiledFunction, SymbolTable, ExprNode,
} from "@next-tutor/domain";
export {
  applyMathCommand, axisVariables, buildEvaluationContext, compileExpression, createMathDocument, DEFAULT_STYLE,
  DEFAULT_VIEW_2D, DEFAULT_CAMERA_3D, DEFAULT_RENDER_3D,
  deserializeMathDocument, DRAWING_MODES, evaluateExpression, MATH_LIMITS, presetDocument, resolveGeometry,
  resolveGeometry2D, sampleExplicit, sampleInverse, sampleParametric2D, samplePolar, implicitContour,
  newDefinitionId, parseEquation, canonicalSource, semanticFingerprint, serializeMathDocument, validateMathDocument,
  generateExplicitSurface, generateParametricSurface, generateImplicitSurface, generateParametricCurve,
  mathToThree, threeToMath, v3,
} from "@next-tutor/domain";
