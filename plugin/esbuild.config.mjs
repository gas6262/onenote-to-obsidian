import esbuild from "esbuild";
import process from "process";
import builtins from "builtin-modules";
import { copyFileSync, mkdirSync, existsSync } from "fs";

const prod = process.argv[2] === "production";
// Point this at a vault to build straight into it:
//   QUIRE_VAULT=/path/to/vault npm run build
const VAULT_PLUGIN_DIR = process.env.QUIRE_VAULT
  ? `${process.env.QUIRE_VAULT}/.obsidian/plugins/quire`
  : "dist";

const copyStatic = {
  name: "copy-static",
  setup(build) {
    build.onEnd(() => {
      if (!existsSync(VAULT_PLUGIN_DIR)) mkdirSync(VAULT_PLUGIN_DIR, { recursive: true });
      for (const f of ["manifest.json", "styles.css"]) {
        try { copyFileSync(f, `${VAULT_PLUGIN_DIR}/${f}`); } catch {}
      }
      console.log(`[quire] built -> ${VAULT_PLUGIN_DIR}`);
    });
  },
};

const ctx = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  external: ["obsidian", "electron", "@codemirror/*", "@lezer/*", ...builtins],
  format: "cjs",
  target: "es2021",
  logLevel: "info",
  sourcemap: prod ? false : "inline",
  treeShaking: true,
  minify: prod,
  outfile: `${VAULT_PLUGIN_DIR}/main.js`,
  plugins: [copyStatic],
});

if (prod) { await ctx.rebuild(); process.exit(0); }
else await ctx.watch();
