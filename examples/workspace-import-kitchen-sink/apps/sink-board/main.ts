import { join } from "lodash";

export function template(title: string): string {
  return `<main class="sink-board"><h1>${join([title], "")}</h1></main>`;
}

export const appName = "sink-board";
