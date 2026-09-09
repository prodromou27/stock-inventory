// Reproducible local distribution; no runtime CDN or remote editor service.
const fs = require('node:fs');
const path = require('node:path');
const target = path.join(__dirname, '../static/vendor/grapesjs');
fs.mkdirSync(target, {recursive: true});
for (const [from, to] of [['dist/grapes.min.js', 'grapes.min.js'], ['dist/grapes.min.js.map', 'grapes.min.js.map'], ['dist/css/grapes.min.css', 'grapes.min.css'], ['LICENSE', 'LICENSE']]) {
  fs.copyFileSync(path.join(__dirname, '../node_modules/grapesjs', from), path.join(target, to));
}
const bundled = ['backbone','backbone-undo','underscore','codemirror','codemirror-formatting','html-entities','promise-polyfill'];
const notices = bundled.map(name => {
  const dir = path.join(__dirname, '../node_modules', name);
  const pkg = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
  const files = fs.readdirSync(dir).filter(file => /^(license|readme)/i.test(file));
  return `${name} ${pkg.version}\nLicense: ${JSON.stringify(pkg.license || pkg.licenses)}\n` +
    files.map(file => fs.readFileSync(path.join(dir,file),'utf8')).join('\n');
});
fs.writeFileSync(path.join(target, 'THIRD-PARTY-NOTICES.txt'), notices.join('\n\n'));
const icons = path.join(__dirname, '../node_modules/font-awesome');
fs.cpSync(path.join(icons,'css'),path.join(target,'font-awesome/css'),{recursive:true});
fs.cpSync(path.join(icons,'fonts'),path.join(target,'font-awesome/fonts'),{recursive:true});
fs.copyFileSync(path.join(icons,'README.md'),path.join(target,'font-awesome/README.md'));
