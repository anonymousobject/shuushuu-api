"""s9e XML → markdown, using real phpBB post_text samples."""

from app.services.forum_import.s9e_convert import s9e_to_markdown


def test_plain_text():
    assert s9e_to_markdown("<t>.</t>") == "."


def test_plain_text_with_br():
    assert s9e_to_markdown("<t>line1<br/>line2</t>") == "line1\nline2"


def test_bold():
    xml = "<r><B><s>[b]</s>Thank you, Myu-chan!!!!!   ^_^<e>[/b]</e></B></r>"
    assert s9e_to_markdown(xml) == "**Thank you, Myu-chan!!!!!   ^_^**"


def test_italic():
    assert s9e_to_markdown("<r><I><s>[i]</s>hi<e>[/i]</e></I></r>") == "*hi*"


def test_url_link():
    xml = '<r><URL url="http://www.zerochan.net/937289">http://www.zerochan.net/937289</URL></r>'
    assert s9e_to_markdown(xml) == "[http://www.zerochan.net/937289](http://www.zerochan.net/937289)"


def test_img_becomes_link_to_src():
    xml = (
        '<r><IMG src="http://i.imgur.com/PmlV8Ns.gif"><s>[img]</s>'
        '<URL url="http://i.imgur.com/PmlV8Ns.gif">http://i.imgur.com/PmlV8Ns.gif</URL>'
        "<e>[/img]</e></IMG></r>"
    )
    assert s9e_to_markdown(xml) == "[image](http://i.imgur.com/PmlV8Ns.gif)"


def test_nested_quote():
    xml = (
        '<r><QUOTE author="Fuwari"><s>[quote="Fuwari"]</s>'
        '<QUOTE author="Oni"><s>[quote="Oni"]</s>Does anyone know?<e>[/quote]</e></QUOTE>'
        "Found it.<e>[/quote]</e></QUOTE>Thankies</r>"
    )
    assert s9e_to_markdown(xml) == (
        '[quote="Fuwari"][quote="Oni"]Does anyone know?[/quote]Found it.[/quote]Thankies'
    )


def test_emoji_kept_as_text():
    assert s9e_to_markdown("<r><E>:lol:</E></r>") == ":lol:"


def test_color_stripped_to_text():
    xml = '<r><COLOR color="darkgreen"><s>[color=darkgreen]</s>18<e>[/color]</e></COLOR></r>'
    assert s9e_to_markdown(xml) == "18"


def test_list_becomes_dashes():
    xml = (
        "<r>nominates:\n<LIST><s>[list]</s>\n"
        "<LI><s>[*]</s>Kagemaru</LI>\n<LI><s>[*]</s>Amfest</LI><e>[/list]</e></LIST></r>"
    )
    out = s9e_to_markdown(xml)
    assert "nominates:" in out
    assert "- Kagemaru" in out
    assert "- Amfest" in out


def test_attachment_dropped_inline():
    xml = (
        '<r><ATTACHMENT filename="x.jpg" index="0"><s>[attachment=0]</s>x.jpg'
        "<e>[/attachment]</e></ATTACHMENT></r>"
    )
    assert s9e_to_markdown(xml) == ""


def test_unknown_element_keeps_text():
    # defensive: an unrecognized tag still surfaces its inner text
    assert s9e_to_markdown("<r><WEIRD>kept<br/>text</WEIRD></r>") == "kept\ntext"


def test_malformed_falls_back_to_stripped_text():
    assert s9e_to_markdown("not xml at all") == "not xml at all"


def test_empty():
    assert s9e_to_markdown("") == ""
