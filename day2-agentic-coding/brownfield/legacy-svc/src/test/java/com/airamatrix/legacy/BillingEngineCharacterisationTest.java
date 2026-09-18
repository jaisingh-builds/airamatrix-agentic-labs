package com.airamatrix.legacy;

import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Characterisation tests — TODO(lab).
 *
 * A characterisation test does NOT assert what the code should do. It asserts
 * what the code DOES do, today, bugs included. That is what makes it a
 * safety net: if a refactor changes behaviour, you find out immediately,
 * instead of in the nightly billing run.
 *
 * Write these BEFORE you change a single line of BillingEngine.
 *
 * Work in this order:
 *   1. Pin the behaviour you can see (start with the two given below).
 *   2. Extend coverage to every tier, and to rush and prepaid credits.
 *   3. Only then fix the crash in the defect report.
 *   4. Re-run. A characterisation test that now fails is a behaviour you
 *      changed - decide deliberately whether you meant to.
 */
class BillingEngineCharacterisationTest {

    private final BillingEngine engine = new BillingEngine();

    @Test
    void bronzeNoDiscountUnderAThousandSlides() {
        // 100 slides x 10.46 = 1046.00, +18% tax = 1234.28 ... but this is what
        // it actually returns today. Pin it. Do not "correct" it yet.
        assertEquals(1234.0, engine.calculateInvoice("ACC-1003", 100, 10.46, false, 0), 0.001);
    }

    @Test
    void zeroSlidesReturnsZero() {
        // Ops reported that zero-slide accounts "fail". Does this reproduce?
        assertEquals(0.0, engine.calc("ACC-1001", 0, 10.0), 0.001);
    }

    // ---------------------------------------------------------------- TODO
    // Add characterisation tests for:
    //   - gold, at 100 / 600 / 1200 slides
    //   - silver, at 600 and 1200 slides
    //   - rush handling on top of a discount
    //   - prepaid credits smaller than, equal to, and larger than the total
    //   - the account whose tier is null

    @Test
    @Disabled("TODO(lab): remove @Disabled once you have pinned current behaviour above")
    void nullTierAccountCurrentlyThrows() {
        // The defect report's crash. Pin it as the CURRENT behaviour first...
        assertThrows(NullPointerException.class,
                     () -> engine.calc("ACC-1099", 100, 10.0));
        // ...then fix BillingEngine, and change this test to assert the
        // behaviour you decided on. What SHOULD an untiered account be charged?
        // That is a product question, not a code question. Write down your
        // assumption in the review note.
    }
}
