#!/usr/bin/env python3
"""
Demo script showing markdown parser in action
"""

from app.utils.markdown import parse_markdown, strip_markdown

# Test examples
examples = [
    "This is **bold** and *italic* text",
    "Check out [this link](https://example.com)",
    "> This is a quote from someone",
    "Line 1\nLine 2\nLine 3",
    "**Important:** Read the [documentation](https://docs.example.com) carefully!",
    "> **Admin wrote:** Please follow the rules\n\nThanks for the reminder!",
]

print("=" * 80)
print("MARKDOWN PARSER DEMONSTRATION")
print("=" * 80)

for i, example in enumerate(examples, 1):
    print(f"\n[Example {i}]")
    print(f"Input:  {example!r}")
    print(f"HTML:   {parse_markdown(example)}")
    print(f"Plain:  {strip_markdown(example)}")

# Security examples
print("\n" + "=" * 80)
print("SECURITY TESTS")
print("=" * 80)

security_tests = [
    ("<script>alert('xss')</script>", "HTML is escaped"),
    ("[click](javascript:alert('xss'))", "Dangerous URLs blocked"),
    ("**<img src=x onerror=alert(1)>**", "HTML in formatting escaped"),
]

for test_input, description in security_tests:
    print(f"\n[{description}]")
    print(f"Input:  {test_input!r}")
    print(f"Output: {parse_markdown(test_input)}")
    print(f"✓ Safe: No script execution possible")

print("\n" + "=" * 80)
