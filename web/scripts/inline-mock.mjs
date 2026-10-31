// Packs the mock build into one HTML file for publishing as an artifact.
// React loads from cdnjs (pinned); everything else is inlined.
import { readFileSync, writeFileSync, readdirSync } from 'node:fs'

const dir = 'dist-mock'
const files = readdirSync(dir)
const js = readFileSync(`${dir}/app.js`, 'utf8').replace(/<\/script/gi, '<\\/script')
const cssFile = files.find((f) => f.endsWith('.css'))
const css = cssFile ? readFileSync(`${dir}/${cssFile}`, 'utf8') : ''
const out = process.argv[2] ?? `${dir}/backchannel.html`
const local = process.argv.includes('--local')
const react = local ? '../node_modules/react/umd/react.production.min.js' : 'https://cdnjs.cloudflare.com/ajax/libs/react/18.3.1/umd/react.production.min.js'
const reactDom = local
  ? '../node_modules/react-dom/umd/react-dom.production.min.js'
  : 'https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.3.1/umd/react-dom.production.min.js'

const html = `<title>Backchannel</title>
<meta name="description" content="A Slack-style home for your iMessages">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lato:ital,wght@0,400;0,700;0,900;1,400&display=swap" rel="stylesheet">
<style>${css}</style>
<div id="root"></div>
<script src="${react}"></script>
<script src="${reactDom}"></script>
<script>${js}</script>
`
writeFileSync(out, html)
console.log(`wrote ${out} (${(html.length / 1024).toFixed(0)} KB)`)
