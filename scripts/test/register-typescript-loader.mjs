/** Register the local TypeScript loader without Node's experimental-loader warning. */
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register(new URL("./typescript-loader.mjs", import.meta.url), pathToFileURL(`${process.cwd()}/`));
