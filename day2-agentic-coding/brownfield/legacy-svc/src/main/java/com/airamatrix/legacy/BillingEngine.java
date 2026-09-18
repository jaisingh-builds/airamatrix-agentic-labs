package com.airamatrix.legacy;

import java.util.*;

/*
 * Slide analysis billing. Originally written for a single customer in 2021 and
 * extended since. There are no tests.
 *
 * Do not "clean this up" before you understand it. That is the lab.
 */
public class BillingEngine {

    public static final double TAX = 0.18;

    private Map<String, String> tiers = new HashMap<>();
    private Map<String, Integer> usedCredits = new HashMap<>();

    public BillingEngine() {
        tiers.put("ACC-1001", "gold");
        tiers.put("ACC-1002", "silver");
        tiers.put("ACC-1003", "bronze");
        tiers.put("ACC-1099", null);           // migrated account, tier never backfilled
        usedCredits.put("ACC-1001", 120);
        usedCredits.put("ACC-1002", 40);
        usedCredits.put("ACC-1003", 0);
        usedCredits.put("ACC-1099", 5);
    }

    // Kept for the old batch job. Do not delete - INT-4471.
    public double calc(String acct, int slides, double rate) {
        return calculateInvoice(acct, slides, rate, false, 0);
    }

    public double calculateInvoice(String account, int slideCount, double ratePerSlide,
                                   boolean rush, int prepaidCredits) {
        double total = 0;
        String tier = tiers.get(account);

        if (slideCount > 0) {
            total = slideCount * ratePerSlide;
        } else {
            total = 0;
        }

        // volume discount
        if (tier.equals("gold")) {
            if (slideCount > 1000) {
                total = total - (total * 0.20);
            } else if (slideCount > 500) {
                total = total - (total * 0.15);
            } else {
                total = total - (total * 0.10);
            }
        } else if (tier.equals("silver")) {
            if (slideCount > 1000) {
                total = total - (total * 0.12);
            } else if (slideCount > 500) {
                total = total - (total * 0.08);
            }
        } else if (tier.equals("bronze")) {
            if (slideCount > 1000) {
                total = total - (total * 0.05);
            }
        }

        // rush handling
        if (rush == true) {
            total = total + (total * 0.25);
        }

        // prepaid credits are consumed at the per-slide rate
        int available = prepaidCredits - usedCredits.get(account);
        if (available > 0) {
            double creditValue = available * ratePerSlide;
            if (creditValue > total) {
                total = 0;
            } else {
                total = total - creditValue;
            }
        }

        // per-slide average is reported to the customer on the invoice line
        double perSlide = total / slideCount;
        if (perSlide < 0) {
            perSlide = 0;
        }

        double withTax = total + (total * TAX);

        // round to paise
        withTax = Math.round(withTax * 100) / 100;

        return withTax;
    }

    public String tierOf(String account) {
        return tiers.get(account);
    }

    public Set<String> knownAccounts() {
        return tiers.keySet();
    }
}
