/**
 * Platform Scope for App Code Runtime
 *
 * Creates the scope object that gets injected into the runtime.
 * All platform APIs are bundled here for easy injection.
 */

import {
	useLocation,
	useMatch,
	useResolvedPath,
	Outlet,
	useOutletContext,
} from "react-router-dom";
import { useWorkflowQuery } from "./useWorkflowQuery";
import { useWorkflowMutation } from "./useWorkflowMutation";
import { useParams } from "./useParams";
import { useSearchParams } from "./useSearchParams";
import { navigate, useNavigate } from "./navigate";
import { useUser } from "./useUser";
import { RequireRole } from "./RequireRole";
import { useAppState } from "./useAppState";
import { Link, NavLink, Navigate } from "./navigation";
import { tables } from "../app-sdk/tables";
import { useTable } from "../app-sdk/use-table";
import { useInfiniteTable } from "../app-sdk/use-infinite-table";

/**
 * Platform scope object containing all platform APIs
 *
 * This is merged with React hooks and UI components to create
 * the full scope available to user code.
 *
 * @example
 * ```typescript
 * // In app-code-runtime.ts
 * const scope = {
 *   ...createPlatformScope(),
 *   React,
 *   useState: React.useState,
 *   useEffect: React.useEffect,
 *   ...UIComponents,
 * };
 * ```
 */
export function createPlatformScope(): Record<string, unknown> {
	return {
		// Workflow execution
		useWorkflowQuery,
		useWorkflowMutation,

		// React Router - ALL commonly used exports
		useLocation,
		useParams,
		useSearchParams,
		useMatch,
		useResolvedPath,
		useOutletContext,
		navigate,
		useNavigate,
		Outlet,
		Link,
		NavLink,
		Navigate,

		// User context
		useUser,

		// Role guards
		RequireRole,

		// App state
		useAppState,

		// Table access
		tables,
		useTable,
		useInfiniteTable,
	};
}
