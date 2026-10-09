/**
 * Pure cross-client domain logic: knowledge-graph DAG layout geometry,
 * semantic label mappings and display projections. No platform APIs here.
 */
export const DOMAIN_PACKAGE_VERSION = "0.2.0";

export * from "./graph-layout.ts";
export * from "./labels.ts";
export * from "./chem-lab/index.ts";
export * from "./electrical-lab/index.ts";
