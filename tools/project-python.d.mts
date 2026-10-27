export const PROJECT_ROOT: string;
export const MINIMUM_PYTHON: readonly [number, number];
export const PYTHON_REPAIR: string;
export class ProjectPythonError extends Error {
  code: "project_python_unavailable";
  attempts: { source: string; executable: string; problem: string }[];
}
export interface ResolvedPython { executable: string; version: string; resolved: string; source: string }
export function projectVenvPython(root?: string, platform?: string): string;
export function resolveProjectPython(options?: { aliases?: string[]; environment?: Record<string, string | undefined>;
  root?: string; platform?: string; exists?: (file: string) => boolean;
  probe?: (executable: string) => { ok: boolean; version?: string; resolved?: string; problem?: string } }): ResolvedPython;
export function projectPython(aliases?: string[]): string;
