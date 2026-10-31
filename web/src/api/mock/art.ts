// Illustrated stand-ins for photos and contact pictures in the mock.
// Each returns an SVG data: URI, so the mock ships as one self-contained file.

const uri = (svg: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`

export function portrait(o: { bg: [string, string]; skin: string; hair: string; shirt: string; style?: 'short' | 'long' | 'bun' | 'none' }) {
  const hair =
    o.style === 'long'
      ? `<path d="M30 58c-6-26 6-40 34-40s40 14 34 40c-2 20-6 36-6 36H36s-4-16-6-36z" fill="${o.hair}"/>`
      : o.style === 'bun'
        ? `<circle cx="64" cy="22" r="13" fill="${o.hair}"/><path d="M36 54c0-20 12-30 28-30s28 10 28 30c-8-10-18-14-28-14s-20 4-28 14z" fill="${o.hair}"/>`
        : o.style === 'none'
          ? ''
          : `<path d="M36 54c-2-22 10-32 28-32s30 10 28 32c-6-9-16-13-28-13s-22 4-28 13z" fill="${o.hair}"/>`
  return uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${o.bg[0]}"/><stop offset="1" stop-color="${o.bg[1]}"/></linearGradient></defs>
<rect width="128" height="128" fill="url(#g)"/>
<ellipse cx="64" cy="128" rx="46" ry="34" fill="${o.shirt}"/>
<rect x="56" y="78" width="16" height="18" rx="6" fill="${o.skin}"/>
<circle cx="64" cy="58" r="26" fill="${o.skin}"/>
${hair}
</svg>`)
}

export const scenes = {
  garden: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#bfe0f2"/><stop offset="1" stop-color="#f6efd9"/></linearGradient></defs>
<rect width="800" height="600" fill="url(#s)"/>
<circle cx="640" cy="120" r="56" fill="#ffe7a3"/>
<rect y="430" width="800" height="170" fill="#7a5134"/>
<rect y="420" width="800" height="22" fill="#5f3f28"/>
<g stroke="#3f7d3a" stroke-width="10" fill="none" stroke-linecap="round">
<path d="M180 430c0-80 20-160-10-260"/><path d="M180 330c-40-20-60-40-70-70"/><path d="M178 280c40-10 60-40 70-70"/>
<path d="M400 430c0-90-10-170 20-270"/><path d="M402 320c-50-10-70-40-80-80"/><path d="M410 250c40 0 70-30 80-60"/>
<path d="M620 430c0-70 10-140-20-230"/><path d="M618 330c40-20 60-40 70-70"/><path d="M612 270c-40-10-60-30-70-60"/>
</g>
<g fill="#4f9a45"><ellipse cx="120" cy="250" rx="34" ry="18"/><ellipse cx="250" cy="200" rx="34" ry="18"/><ellipse cx="320" cy="230" rx="34" ry="18"/><ellipse cx="500" cy="180" rx="34" ry="18"/><ellipse cx="690" cy="250" rx="34" ry="18"/><ellipse cx="540" cy="200" rx="34" ry="18"/></g>
<g fill="#d8432f"><circle cx="150" cy="300" r="26"/><circle cx="205" cy="360" r="22"/><circle cx="380" cy="270" r="28"/><circle cx="440" cy="340" r="24"/><circle cx="650" cy="300" r="26"/><circle cx="590" cy="360" r="20"/></g>
<g fill="#f08a5d"><circle cx="232" cy="250" r="18"/><circle cx="470" cy="230" r="16"/></g>
<rect x="88" y="150" width="12" height="290" fill="#a77b4f"/><rect x="700" y="170" width="12" height="270" fill="#a77b4f"/>
</svg>`),
  house: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9ccbe8"/><stop offset="1" stop-color="#e8f3f8"/></linearGradient></defs>
<rect width="800" height="600" fill="url(#s)"/>
<rect y="470" width="800" height="130" fill="#8fbf6a"/>
<path d="M200 260 400 120 600 260z" fill="#38485a"/>
<rect x="230" y="255" width="340" height="230" fill="#f3e6d2"/>
<rect x="370" y="350" width="64" height="135" rx="4" fill="#2f6f73"/><circle cx="420" cy="420" r="4" fill="#f3c969"/>
<rect x="262" y="300" width="70" height="60" fill="#cfe6f2" stroke="#ffffff" stroke-width="6"/>
<rect x="468" y="300" width="70" height="60" fill="#cfe6f2" stroke="#ffffff" stroke-width="6"/>
<path d="M370 485h64l30 115H340z" fill="#d9c7a8"/>
<g fill="#5f9b4a"><circle cx="230" cy="480" r="38"/><circle cx="290" cy="490" r="30"/><circle cx="560" cy="485" r="36"/><circle cx="510" cy="492" r="26"/></g>
<rect x="520" y="150" width="36" height="70" fill="#38485a"/>
</svg>`),
  pie: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
<defs><linearGradient id="w" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#b9875a"/><stop offset="1" stop-color="#8a5a35"/></linearGradient></defs>
<rect width="800" height="600" fill="url(#w)"/>
<g stroke="#7a4d2c" stroke-width="3" opacity=".5"><path d="M0 120h800"/><path d="M0 260h800"/><path d="M0 400h800"/><path d="M0 540h800"/></g>
<ellipse cx="400" cy="320" rx="300" ry="220" fill="#f4f1ea"/>
<ellipse cx="400" cy="310" rx="240" ry="170" fill="#d99a52"/>
<ellipse cx="400" cy="305" rx="205" ry="140" fill="#8c3b2a"/>
<g stroke="#e6ad64" stroke-width="26" stroke-linecap="round">
<path d="M260 210l240 200"/><path d="M320 180l230 190"/><path d="M220 270l200 165"/>
<path d="M540 210 300 410"/><path d="M480 180 250 370"/><path d="M580 270 380 435"/>
</g>
<ellipse cx="400" cy="310" rx="240" ry="170" fill="none" stroke="#c9843f" stroke-width="22" stroke-dasharray="6 26" stroke-linecap="round"/>
</svg>`),
  noodles: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 420" width="800" height="420">
<rect width="800" height="420" fill="#1f2a2e"/>
<circle cx="640" cy="80" r="140" fill="#2c3b40"/>
<path d="M210 210h380c0 110-85 170-190 170s-190-60-190-170z" fill="#e9e1d4"/>
<path d="M210 210h380" stroke="#c43d2f" stroke-width="16"/>
<g stroke="#f0c873" stroke-width="9" fill="none" stroke-linecap="round">
<path d="M250 205c30-30 50 30 80 0s50 30 80 0 50 30 80 0 50 30 70 0"/>
<path d="M260 190c30-30 50 30 80 0s50 30 80 0 50 30 80 0 50 30 60 0"/>
</g>
<g fill="#6aa84f"><circle cx="330" cy="185" r="12"/><circle cx="470" cy="180" r="10"/><circle cx="420" cy="190" r="9"/></g>
<path d="M560 60 400 190M600 70 440 195" stroke="#b07a4a" stroke-width="10" stroke-linecap="round"/>
<g stroke="#ffffff" stroke-width="5" fill="none" opacity=".35" stroke-linecap="round"><path d="M320 140c-20-20 20-40 0-60"/><path d="M400 130c-20-20 20-40 0-60"/><path d="M480 140c-20-20 20-40 0-60"/></g>
</svg>`),
  field: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" height="600">
<rect width="800" height="600" fill="#4f9d4a"/>
<g fill="#5aab55"><rect x="0" width="100" height="600"/><rect x="200" width="100" height="600"/><rect x="400" width="100" height="600"/><rect x="600" width="100" height="600"/></g>
<g stroke="#f4f7f2" stroke-width="8" fill="none"><rect x="40" y="40" width="720" height="520"/><path d="M400 40v520"/><circle cx="400" cy="300" r="90"/><rect x="40" y="190" width="110" height="220"/><rect x="650" y="190" width="110" height="220"/></g>
<circle cx="520" cy="380" r="34" fill="#ffffff"/>
<path d="m520 362 14 10-5 17h-18l-5-17z" fill="#222"/>
</svg>`),
}

export const avatars = {
  jordan: portrait({ bg: ['#f6c28b', '#e98a5e'], skin: '#c98d65', hair: '#2b1d16', shirt: '#2f5d8a' }),
  priya: portrait({ bg: ['#b9d7c9', '#7fb3a0'], skin: '#a86f4c', hair: '#1c1412', shirt: '#9b3d5c', style: 'long' }),
  mom: portrait({ bg: ['#f3d6e4', '#d79bbb'], skin: '#e8bf9c', hair: '#3a2a22', shirt: '#5a6fa8', style: 'bun' }),
  grace: portrait({ bg: ['#d6dcf7', '#9aa7e6'], skin: '#efc7a5', hair: '#20160f', shirt: '#e0a23b', style: 'long' }),
  ellie: portrait({ bg: ['#fde3b5', '#f2b46a'], skin: '#f1cfae', hair: '#5b3a24', shirt: '#3e8e7e', style: 'bun' }),
}

// Original sample custom emoji and stickers for the mock (simple shapes, nothing borrowed).
export const emojiArt = {
  yayBlob: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
<path d="M10 22 4 10M54 22l6-12" stroke="#f0a020" stroke-width="6" stroke-linecap="round"/>
<ellipse cx="32" cy="38" rx="24" ry="21" fill="#ffc83d"/><ellipse cx="32" cy="44" rx="18" ry="12" fill="#ffd96a"/>
<circle cx="24" cy="33" r="3.5" fill="#3a2a10"/><circle cx="40" cy="33" r="3.5" fill="#3a2a10"/>
<path d="M23 42q9 9 18 0" stroke="#3a2a10" stroke-width="3.5" fill="none" stroke-linecap="round"/></svg>`),
  lgtm: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"><rect x="2" y="16" width="60" height="32" rx="9" fill="#1e8a5a"/>
<text x="32" y="39" font-family="Arial, sans-serif" font-size="17" font-weight="900" fill="#fff" text-anchor="middle">LGTM</text></svg>`),
  nice: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"><rect x="2" y="16" width="60" height="32" rx="16" fill="#7a4fd0"/>
<text x="32" y="38.5" font-family="Arial, sans-serif" font-size="17" font-weight="900" font-style="italic" fill="#fff" text-anchor="middle">nice</text></svg>`),
  hype: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"><defs><linearGradient id="f" x1="0" y1="1" x2="0" y2="0">
<stop offset="0" stop-color="#e2361e"/><stop offset="1" stop-color="#ffb21e"/></linearGradient></defs>
<path d="M32 4c4 12 18 18 18 34a18 18 0 0 1-36 0c0-9 5-13 8-18 1 6 4 8 6 8-2-8 0-16 4-24z" fill="url(#f)"/>
<path d="M32 30c3 6 9 9 9 17a9 9 0 0 1-18 0c0-5 3-8 5-11 1 3 2 4 4 4-1-4 0-7 0-10z" fill="#fff3b0"/></svg>`),
}

export const stickerArt = {
  pizza: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<path d="M64 120 14 22q50-24 100 0z" fill="#f2c14e" stroke="#fff" stroke-width="6" stroke-linejoin="round"/>
<path d="M14 22q50-24 100 0l-6 12q-44-20-88 0z" fill="#c9853c"/>
<g fill="#c8382c"><circle cx="50" cy="48" r="9"/><circle cx="78" cy="50" r="8"/><circle cx="64" cy="76" r="8"/><circle cx="62" cy="100" r="5"/></g></svg>`),
  coolDog: uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<ellipse cx="30" cy="40" rx="16" ry="28" fill="#8a5a32" transform="rotate(-20 30 40)"/><ellipse cx="98" cy="40" rx="16" ry="28" fill="#8a5a32" transform="rotate(20 98 40)"/>
<circle cx="64" cy="66" r="44" fill="#d9a066"/><ellipse cx="64" cy="88" rx="24" ry="18" fill="#f1d3ad"/>
<ellipse cx="64" cy="78" rx="9" ry="6" fill="#2b1d16"/><path d="M54 94q10 7 20 0" stroke="#2b1d16" stroke-width="4" fill="none" stroke-linecap="round"/>
<rect x="28" y="50" width="30" height="18" rx="7" fill="#111"/><rect x="70" y="50" width="30" height="18" rx="7" fill="#111"/><path d="M58 56h12" stroke="#111" stroke-width="5"/></svg>`),
}
