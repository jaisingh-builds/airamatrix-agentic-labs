# Defect report — INT-4488

**Reported by:** Billing ops
**Severity:** high — the nightly invoice run aborts, so nothing downstream bills

## What happens

The nightly run dies partway through. See `stack-trace.txt`.

## Also reported, possibly unrelated

Two other complaints arrived the same week. Nobody has confirmed whether they are
the same bug:

1. **"Invoices for zero-slide accounts fail."** An account with a cancelled batch
   (0 slides) sometimes errors instead of producing a zero invoice.
2. **"The rounding is wrong."** A customer says a ₹1,234.567 invoice came through
   as ₹1,234.00, not ₹1,234.57. Small, but they reconcile to the paisa.

## What we know

- `BillingEngine` has no tests.
- It is called from the nightly batch job and from the customer portal.
- `calc(...)` is used by an old batch job. INT-4471 says do not delete it.

## What is wanted

A fix for the crash, without changing behaviour anyone depends on.
