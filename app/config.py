"""Copyright (c) 2026 Zhi Lin. All rights reserved.
Author: zhi_lin@qq.com
"""

"""Foxit PDF SDK configuration."""

import os

# Foxit PDF SDK License
FOXIT_SN = os.environ.get(
    "FOXIT_SN",
    "qHWsw8KJq+YAazQTdZWiJdN82s5k/rMYvSmAkyw7uezckkOv1j7eEQ=="
)

FOXIT_KEY = os.environ.get(
    "FOXIT_KEY",
    "8f3g1sNtvW8NAgfqzg/lUms2iYPp8YdwmamVG82GVqXIxvtTMQGsYQuf41wHO+ZSb75culm2Ii2tYec/CdsM+l+cU7YFZVma35Q8MMmO7rLe5oTHm1VcULosy3PpVcuEKsy52kZA1iMeAAR7KXMaCFppbTxhliYBmhdiSRblHIl2dyb9rLwrDiApltm82eMle/9VWwjPPjynCSJAeZ3myLjyz1mJRAwJKVN96mTxuSQeceFkQqppygv83Fvgf4Cib1BY4JVcuhcabXg6yc2rTWfvxnWsPKLTwfsVHW8zFD42Kjbbm3L7KEHsL7sZMnezTQU5/z2a2m/eybcfxP6ML4Yfg4tsIZr6fHxh/FxT1VQZy1NKS/voU0Z7WaxYNmqWASkzlxEhoyx9tia+SkL9aTHx3do9oFKol8L5MarYA+bPPfgkpnuVe5A2hgxG4D3oQYC8cp2bZATO21Oyu43wi4J2TZMM1WdE17qzZsv3Vl76NJ2vJ99G9Y+jqKs4Dt2JSaiGcKT6nFjgSr+8NOwhsYnab0ZQxJOs/aGog78jkoIqIibnPB3hz8U+XdfEWoZsezFrT4uaGRjkojdiH5sspXrgeUFV3YPNAtXEV4fHNYfn3u1/IgMQimCSe8CR+RqurhlUYSd4lZ7DyaHF8l/B2s9LNze/tdx3y+fRULSA54ABdRYG+AhA2m3plpirSXoJwUGVWEPifCvLaOSilgwhOCZ0K+DE9WK5I7P0oQbkumWJCGqKzsfhTqAZVYi+sWMCAV21IZbBhOUVvpGTOzEZ/qFs4QG9IYOPrdpObtHgZQvr87sro1EvN+vqG8TuX6+iB40J+FcVtmXB9d4mNoeZ16qR1tDd00q+R4TaCI013JnarxOVgq5jhovLgNgClqGCDD9jVUSAniCQDnErmg6GHca/aUwqSDhgA6ucJCq8O+PlBbW03SnGk+Bwte3JR77BfmW+tKpiA+kADX3wQIM06skbqmQJh8/kXEVNnafsjwauIPnE7M4Hc3Nn9JKQqZ5Ja8OzNstuxWN69I1VebVP92bovGcOg0afxkp6UTf1j3v+5pZYHUzmK7vcl3nt76RYv7cV3S2gqZKO9dUu8rd/poUL8SUIoN/LY92BFH7iiFsCkBSkPuCMJ/86cxxgfjZjObR0sGxEw4uoP/YbubFYWFVnIuXQOdwvsec3HJkK0IXR4HGtNlGJFMY4IWKwH5oPELkHjTYD5hHIZVHGoYp1YBvGNtretmc2UisJFTQoRLwefayeYJb7VuSxLF9ZyqyfIWfEKQFgJZHzO0wcfC/QTkqTusxE0LPGxGegcYMC0SwFWswNY1/GVim8/4/krF0QxGHtifXSv7h4yRb677XrN1hvFuKCiDQqaKmCXq05YstQCU30p+WiByLYlcRqYkA6+HmPU5yVffUNN57kmmPXoneqkRYHDyO0JIB7uwxOQA=="
)

# Upload / output directories
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")

# Ensure directories exist
for d in [UPLOAD_DIR, OUTPUT_DIR, IMAGES_DIR]:
    os.makedirs(d, exist_ok=True)
