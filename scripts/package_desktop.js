#!/usr/bin/env node

const path = require("path");

const { main } = require(path.resolve(__dirname, "..", "desktop_app", "package_desktop.js"));

try {
  main(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
}
