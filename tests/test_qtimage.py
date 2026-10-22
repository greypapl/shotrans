from PIL import Image

from shotrans.qtimage import pil_to_qimage, qimage_to_pil


def test_roundtrip_keeps_pixels(qapp):
    img = Image.new("RGB", (37, 11), (10, 200, 30))
    img.putpixel((5, 7), (255, 0, 128))
    back = qimage_to_pil(pil_to_qimage(img))
    assert back.size == (37, 11)
    assert back.getpixel((5, 7)) == (255, 0, 128)
    assert back.getpixel((0, 0)) == (10, 200, 30)
