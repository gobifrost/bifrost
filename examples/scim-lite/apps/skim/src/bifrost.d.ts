// Local-only tsc stub for the platform-provided "bifrost" SDK.
// `bifrost solution start` / deploy installs the real SDK transiently;
// this file exists so `npx tsc` passes in isolation. Keep shapes honest.
/// <reference types="vite/client" />
declare module "bifrost" {
  import type { ComponentType, ReactNode } from "react";

  export const BifrostProvider: ComponentType<Record<string, unknown>>;
  export const BifrostHeader: ComponentType<Record<string, unknown>>;

  export function useUser(): {
    id?: string;
    email?: string;
    organizationId?: string;
    isPlatformAdmin?: boolean;
    roles?: string[];
    isLoading: boolean;
    error?: { message: string } | null;
  };
  export function RequireRole(props: {
    role: string;
    children: ReactNode;
    fallback?: ReactNode;
  }): JSX.Element;
  export function useOrganizations(args?: {
    enabled?: boolean;
    includeInactive?: boolean;
  }): { data?: { items?: { id: string; name: string }[] } };
  export function useWorkflowQuery<T>(ref: string, params?: unknown): {
    data?: T;
    loading: boolean;
    error?: { message: string } | null;
    refresh: () => void;
  };
  export function useWorkflowMutation<T = unknown>(ref: string): {
    mutate: (input: unknown) => void;
    loading: boolean;
    error?: { message: string } | null;
    data?: T | null;
  };
}
