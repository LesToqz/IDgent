// Entry point so `node --test tests/js` (a directory, which node 22 resolves to this file)
// runs every *.test.mjs in this folder. `node --test tests/js/*.test.mjs` works too.
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

for (const name of fs.readdirSync(__dirname).filter((f) => f.endsWith('.test.mjs')).sort()) {
  import(pathToFileURL(path.join(__dirname, name)).href);
}
