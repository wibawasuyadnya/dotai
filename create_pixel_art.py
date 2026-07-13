from PIL import Image, ImageDraw

# Create a new image with a light blue background
img = Image.new('RGB', (100, 100), color=(135, 206, 235))
draw = ImageDraw.Draw(img)

# Draw a very simplified, pixelated version of the statue
# Base/Pedestal
draw.rectangle([(30, 80), (70, 100)], fill=(100, 100, 100))
# Statue body
draw.rectangle([(40, 40), (60, 80)], fill=(150, 200, 150))
# Head
draw.rectangle([(45, 30), (55, 40)], fill=(150, 200, 150))
# Crown spikes (simplified)
draw.rectangle([(42, 25), (45, 30)], fill=(150, 200, 150))
draw.rectangle([(48, 20), (52, 30)], fill=(150, 200, 150))
draw.rectangle([(55, 25), (58, 30)], fill=(150, 200, 150))
# Torch arm
draw.rectangle([(60, 40), (70, 50)], fill=(150, 200, 150))
# Torch flame
draw.rectangle([(65, 30), (70, 40)], fill=(255, 200, 0))

# Save the image
img.save('pixelated_liberty.png')
