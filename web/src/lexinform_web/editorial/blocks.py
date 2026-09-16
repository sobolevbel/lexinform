from wagtail import blocks


class CalloutBlock(blocks.StructBlock):
    title = blocks.CharBlock(required=False)
    body = blocks.RichTextBlock(features=["bold", "italic", "link"])

    class Meta:
        icon = "info-circle"
        label = "Callout"


GUIDE_BLOCKS = [
    ("paragraph", blocks.RichTextBlock(features=["bold", "italic", "link"])),
    ("callout", CalloutBlock()),
]
