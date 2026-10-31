# Draws the Backchannel app icon and writes mac/AppIcon.icns (+ a 1024px PNG for docs).
import io, struct, sys
from PIL import Image, ImageDraw, ImageFilter

S = 2048  # draw at 2x, downsample for smooth edges
def sc(v): return int(v * S / 1024)

def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def gradient(size, top, bottom):
    g = Image.new('RGB', (1, size[1]))
    for y in range(size[1]):
        g.putpixel((0, y), lerp(top, bottom, y / max(1, size[1] - 1)))
    return g.resize(size)

canvas = Image.new('RGBA', (S, S), (0, 0, 0, 0))

# Body: Apple's icon grid puts an 824px rounded square inside the 1024 canvas.
body_box = (sc(100), sc(100), sc(924), sc(924))
radius = sc(185)
mask = Image.new('L', (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle(body_box, radius=radius, fill=255)

# Soft shadow under the body
shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rounded_rectangle((body_box[0], body_box[1] + sc(14), body_box[2], body_box[3] + sc(14)), radius=radius, fill=(20, 0, 24, 110))
shadow = shadow.filter(ImageFilter.GaussianBlur(sc(18)))
canvas.alpha_composite(shadow)

body = gradient((S, S), (0x5c, 0x1f, 0x66), (0x26, 0x0a, 0x2b)).convert('RGBA')
body.putalpha(mask)
canvas.alpha_composite(body)

d = ImageDraw.Draw(canvas)
PINK = (0xe0, 0x7e, 0xc0, 255)
CREAM = (0xf6, 0xef, 0xe6, 255)
PLUM = (0x3a, 0x13, 0x40, 255)

# Back bubble (pink), lower right, tail pointing down-right
d.rounded_rectangle((sc(430), sc(420), sc(800), sc(690)), radius=sc(96), fill=PINK)
d.polygon([(sc(690), sc(660)), (sc(800), sc(790)), (sc(640), sc(690))], fill=PINK)

# Front bubble (cream), upper left, tail pointing down-left; a thin plum ring separates it from the pink one
ring = sc(16)
d.rounded_rectangle((sc(220) - ring, sc(270) - ring, sc(660) + ring, sc(590) + ring), radius=sc(112) + ring, fill=(0x4a, 0x18, 0x52, 255))
d.polygon([(sc(300) - ring, sc(560)), (sc(232) - ring, sc(724) + ring), (sc(430) + ring, sc(575))], fill=(0x4a, 0x18, 0x52, 255))
d.rounded_rectangle((sc(220), sc(270), sc(660), sc(590)), radius=sc(112), fill=CREAM)
d.polygon([(sc(300), sc(560)), (sc(240), sc(700)), (sc(420), sc(580))], fill=CREAM)

# Typing dots
for cx in (330, 440, 550):
    r = sc(34)
    d.ellipse((sc(cx) - r, sc(430) - r, sc(cx) + r, sc(430) + r), fill=PLUM)

icon = canvas.resize((1024, 1024), Image.LANCZOS)
icon.save(sys.argv[2])

def png(size):
    b = io.BytesIO()
    icon.resize((size, size), Image.LANCZOS).save(b, format='PNG')
    return b.getvalue()

entries = [('icp4', 16), ('icp5', 32), ('ic11', 32), ('ic12', 64), ('ic07', 128), ('ic13', 256), ('ic08', 256), ('ic14', 512), ('ic09', 512), ('ic10', 1024)]
chunks = b''.join(t.encode('ascii') + struct.pack('>I', 8 + len(data)) + data for t, data in ((t, png(n)) for t, n in entries))
with open(sys.argv[1], 'wb') as f:
    f.write(b'icns' + struct.pack('>I', 8 + len(chunks)) + chunks)
print('wrote', sys.argv[1])
