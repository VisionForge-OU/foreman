# Good and Bad Tests

(Adapted from mattpocock/skills `tdd/tests.md` — see NOTICE. Examples kept
language-neutral in spirit; the principles apply to any stack.)

## Good tests

**Integration-style**: test through real interfaces, not mocks of internal parts.

```
# GOOD: tests observable behavior through the public interface
test "user can checkout with valid cart":
    cart = create_cart()
    cart.add(product)
    result = checkout(cart, payment_method)
    assert result.status == "confirmed"
```

Characteristics:

- Tests behavior callers care about
- Uses the public API only
- Survives internal refactors
- Describes WHAT, not HOW
- One logical assertion per test

## Bad tests

**Implementation-detail tests**: coupled to internal structure.

```
# BAD: tests implementation details
test "checkout calls payment_service.process":
    mock_payment = mock(payment_service)
    checkout(cart, payment)
    assert mock_payment.process.called_with(cart.total)
```

Red flags:

- Mocking internal collaborators
- Testing private methods
- Asserting on call counts/order
- Test breaks when refactoring without behavior change
- Test name describes HOW not WHAT
- Verifying through external means instead of the interface

```
# BAD: bypasses the interface to verify
test "create_user saves to database":
    create_user(name="Alice")
    row = db.query("SELECT * FROM users WHERE name = ?", ["Alice"])
    assert row is not None

# GOOD: verifies through the interface
test "create_user makes user retrievable":
    user = create_user(name="Alice")
    retrieved = get_user(user.id)
    assert retrieved.name == "Alice"
```
