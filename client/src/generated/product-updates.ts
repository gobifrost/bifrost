// Generated from product-updates/schema.json; do not edit.
export type BundleSource = { pr?: number; commit?: string; role?: string; };
export type BundleContributor = { login: string; profile_url: string; source_pr: number; role?: string; };
export type BundleAsset = { path: string; url: string; alt: string; caption?: string; };
export type OtherChange = { source: string; title: string; url: string; contributors: BundleContributor[]; };
export type BundleEntry = { id: string; revision: number; published_at: string; title: string; markdown: string; area: "Agents" | "Apps & Forms" | "Workflows" | "Integrations" | "Administration" | "Platform" | "Developer Tools"; type: "New" | "Improved" | "Fixed" | "Security"; action_required: boolean; sources: BundleSource[]; contributors: BundleContributor[]; assets: BundleAsset[]; additional_areas?: string[]; release?: string; };
export type Bundle = { schema_version: 1; target_ref: string; content_ref: string; entries: BundleEntry[]; other_changes: OtherChange[]; };
export type ProductUpdateSource = BundleSource;
export type ProductUpdateContributor = BundleContributor;
export type ProductUpdateAsset = BundleAsset;
export type ProductUpdateEntry = BundleEntry;
export type ProductUpdateOtherChange = OtherChange;
export type ProductUpdatesBundle = Bundle;
