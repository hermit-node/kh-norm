"""Deterministic flat-polygon everyday-object PNG carrier generator.

Draws literal filled pixel polygons with Pillow (NO SVG and NO generative AI).
At most 12 opaque colors appear in any output, comfortably under the 16-color
cap. There is no antialiasing or interpolation step that could expand the palette.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from random import Random
from PIL import Image, ImageDraw

OBJECTS = ('mug','kettle','stapler','desk_lamp','toaster','plant','notebook','wrench','shoe')
# 12 total colors in each palette, including the backdrop (all RGB 1..254).
PALETTES = (
    ('#e8e0cc','#d1c7b0','#c7bca6','#b1a38c','#3b4d58','#516d76',
     '#719098','#8ba8a3','#d9a76d','#c9875d','#6d604e','#f2e6d5'),
    ('#e7e8e3','#cdd2ce','#bac0bd','#aab4b2','#424d56','#596976',
     '#748b97','#a3b7b6','#d3a78d','#aa786c','#777068','#eee1d5'),
    ('#e9e6d9','#d8d3c4','#bdb6a3','#a8a08d','#49544b','#657563',
     '#84947b','#a8ad8e','#c4a46f','#a57a5e','#635b4f','#efe6d1'),
)


def render(seed: str, output: str, width: int = 512, height: int = 512,
           object_type: str = 'random') -> dict:
    if not isinstance(seed,str) or not seed or len(seed)>4096:
        raise ValueError('a nonempty reproducible seed is required')
    if not isinstance(width,int) or not isinstance(height,int) or width<256 or height<128:
        raise ValueError('carrier must be at least 256x128')
    if width*height>24_000_000:
        raise ValueError('image exceeds 24 megapixels')
    if object_type!='random' and object_type not in OBJECTS:
        raise ValueError('unknown everyday-object class')
    destination=Path(output).expanduser().resolve()
    if destination.suffix.lower()!='.png' or destination.exists():
        raise ValueError('use a NEW output .png path')
    random_seed=int.from_bytes(sha256(seed.encode('utf-8')).digest(),'big')
    rng=Random(random_seed)
    name=rng.choice(OBJECTS) if object_type=='random' else object_type
    colors=rng.choice(PALETTES)
    im=Image.new('RGB',(width,height),colors[0])
    d=ImageDraw.Draw(im)
    def shape(points,color):
        xys=[(round((x+rng.uniform(-0.009,0.009))*width),
              round((y+rng.uniform(-0.009,0.009))*height)) for x,y in points]
        d.polygon(xys,fill=colors[color])
    def block(points,color):
        # fixed geometry of this plane (random jitter belongs to seed).
        shape(points,color)
    # Large low-poly background regions, never gradients.
    block([(0,0),(.55,0),(.26,.6),(0,.74)],0)
    block([(.55,0),(1,0),(1,.67),(.26,.6)],1)
    block([(0,.74),(.26,.6),(1,.67),(1,1),(0,1)],2)
    block([(0,.77),(.25,.71),(1,.75),(1,.84),(0,.85)],3)
    # Use the inner 70-80% of the canvas for recognizability.
    if name=='mug':
        block([(.54,.32),(.72,.3),(.79,.36),(.8,.55),(.72,.61),(.6,.60)],4)
        block([(.62,.38),(.70,.37),(.74,.41),(.73,.50),(.68,.54),(.61,.53)],1)
        block([(.28,.31),(.63,.29),(.61,.72),(.55,.8),(.36,.79),(.30,.70)],5)
        block([(.31,.31),(.56,.30),(.55,.77),(.38,.77),(.34,.7)],6)
        block([(.3,.29),(.62,.29),(.65,.33),(.59,.39),(.32,.39),(.27,.34)],4)
        block([(.32,.31),(.58,.31),(.59,.34),(.32,.35)],10)
        block([(.36,.40),(.41,.4),(.43,.7),(.38,.69)],7)
        block([(.34,.78),(.58,.78),(.62,.81),(.29,.82)],10)
    elif name=='kettle':
        block([(.30,.47),(.21,.50),(.14,.44),(.20,.40),(.32,.39)],4)
        block([(.39,.30),(.40,.19),(.57,.18),(.67,.27),(.67,.41),(.6,.40),(.58,.27),(.43,.27)],10)
        block([(.27,.39),(.66,.38),(.72,.52),(.67,.76),(.36,.8),(.25,.67)],5)
        block([(.33,.42),(.58,.41),(.65,.56),(.60,.74),(.37,.75),(.29,.63)],6)
        block([(.46,.32),(.55,.31),(.57,.37),(.45,.39)],4)
        block([(.40,.27),(.60,.27),(.62,.30),(.39,.32)],8)
        block([(.28,.66),(.36,.74),(.52,.78),(.38,.77)],7)
    elif name=='stapler':
        block([(.21,.67),(.8,.61),(.78,.74),(.23,.8)],10)
        block([(.24,.49),(.75,.44),(.82,.5),(.78,.61),(.23,.69),(.18,.62)],4)
        block([(.25,.48),(.70,.42),(.77,.48),(.31,.59)],5)
        block([(.3,.59),(.79,.50),(.77,.59),(.28,.68)],6)
        block([(.26,.72),(.75,.65),(.77,.69),(.27,.77)],8)
        block([(.20,.57),(.23,.56),(.27,.75),(.23,.78)],11)
    elif name=='desk_lamp':
        block([(.38,.29),(.67,.27),(.76,.44),(.31,.48)],4)
        block([(.44,.30),(.65,.3),(.70,.41),(.35,.43)],8)
        block([(.48,.47),(.55,.46),(.56,.71),(.49,.71)],10)
        block([(.36,.73),(.66,.71),(.76,.79),(.29,.82)],5)
        block([(.38,.76),(.65,.75),(.69,.78),(.35,.79)],6)
        block([(.50,.42),(.55,.42),(.57,.48),(.49,.49)],7)
    elif name=='toaster':
        block([(.26,.34),(.66,.29),(.77,.38),(.75,.74),(.64,.8),(.25,.74)],4)
        block([(.30,.35),(.63,.33),(.65,.75),(.29,.72)],5)
        block([(.65,.34),(.73,.40),(.72,.73),(.65,.76)],6)
        block([(.33,.34),(.48,.31),(.53,.36),(.38,.4)],10)
        block([(.50,.32),(.63,.30),(.69,.35),(.55,.39)],10)
        block([(.68,.48),(.73,.48),(.73,.54),(.68,.55)],8)
        block([(.34,.58),(.4,.57),(.4,.64),(.34,.65)],7)
        block([(.33,.74),(.66,.75),(.64,.8),(.35,.8)],10)
    elif name=='plant':
        block([(.41,.50),(.61,.50),(.59,.77),(.45,.8)],8)
        block([(.42,.53),(.59,.54),(.57,.73),(.46,.76)],9)
        block([(.38,.48),(.64,.48),(.64,.56),(.38,.56)],10)
        block([(.49,.5),(.49,.34),(.36,.23),(.45,.23),(.53,.41)],5)
        block([(.52,.52),(.54,.29),(.68,.18),(.70,.27),(.58,.45)],6)
        block([(.48,.43),(.35,.35),(.22,.33),(.29,.48),(.45,.49)],7)
        block([(.53,.43),(.66,.35),(.79,.38),(.7,.50),(.57,.51)],4)
        block([(.51,.30),(.46,.18),(.55,.12),(.61,.22)],6)
    elif name=='notebook':
        block([(.27,.29),(.7,.23),(.75,.75),(.31,.82)],10)
        block([(.29,.29),(.68,.26),(.71,.72),(.34,.78)],11)
        block([(.31,.3),(.35,.77),(.4,.77),(.36,.28)],5)
        block([(.42,.42),(.64,.4),(.65,.44),(.43,.46)],2)
        block([(.42,.52),(.65,.49),(.65,.53),(.42,.56)],2)
        block([(.43,.61),(.62,.59),(.62,.63),(.43,.65)],2)
        block([(.32,.34),(.37,.33),(.37,.38),(.32,.39)],8)
    elif name=='wrench':
        block([(.28,.28),(.36,.17),(.48,.21),(.41,.33),(.44,.43),(.73,.71),(.66,.82),(.58,.78),(.33,.48),(.23,.44)],4)
        block([(.31,.34),(.42,.39),(.67,.7),(.62,.74),(.34,.47)],6)
        block([(.24,.26),(.3,.22),(.36,.29),(.31,.36),(.24,.38),(.21,.32)],8)
        block([(.62,.72),(.67,.69),(.71,.73),(.66,.77)],1)
    elif name=='shoe':
        block([(.15,.55),(.3,.5),(.43,.35),(.56,.42),(.64,.53),(.81,.59),(.84,.74),(.26,.79),(.13,.73)],4)
        block([(.28,.54),(.43,.4),(.56,.48),(.62,.6),(.3,.67)],5)
        block([(.42,.52),(.57,.55),(.61,.59),(.45,.59)],6)
        block([(.19,.7),(.82,.67),(.84,.75),(.2,.78)],11)
        block([(.38,.51),(.47,.55),(.46,.58),(.36,.54)],8)
    destination.parent.mkdir(parents=True,exist_ok=True)
    im.save(destination,format='PNG')
    used=im.getcolors(maxcolors=17)
    if used is None or len(used)>16:
        destination.unlink(missing_ok=True)
        raise AssertionError('polygon rendering exceeded 16-color palette')
    return {'ok':True, 'image':str(destination), 'object':name,
            'width':width,'height':height,'usable_pixels':width*(height-1),
            'colors':len(used),'seed_sha256':sha256(seed.encode()).hexdigest(),
            'algorithm':'direct-polygon-raster-v1','svg_used':False}
