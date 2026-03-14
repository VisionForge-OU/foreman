from hello import greet


def test_greet_returns_hello_with_name():
    assert greet('World') == 'Hello, World!'
