from loom_archiver.names import sanitize, stem


def test_sanitize_replaces_illegal_chars():
    assert sanitize('a/b:c*d?e"f<g>h|i\\j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_collapses_whitespace_and_strips():
    assert sanitize("  hello   world  ") == "hello world"


def test_sanitize_truncates_to_max_len():
    assert len(sanitize("x" * 500, max_len=120)) == 120


def test_sanitize_empty_becomes_placeholder():
    assert sanitize("   ") == "untitled"


def test_stem_prefixes_video_id():
    assert stem("abc123", "My Demo") == "abc123__My Demo"


def test_sanitize_neutralises_path_traversal_segments():
    assert sanitize("..") == "untitled"
    assert sanitize(".") == "untitled"


def test_sanitize_keeps_ordinary_dotted_names():
    assert sanitize("v1.2") == "v1.2"
    assert sanitize(".hidden") == ".hidden"
