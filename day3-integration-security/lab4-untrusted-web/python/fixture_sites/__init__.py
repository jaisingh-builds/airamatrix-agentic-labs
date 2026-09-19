"""The fixture estate for Lab 4: four origins the agent can try to reach.

Three are trusted and on the allow-list. The fourth is an exfiltration sink that
is NOT on the allow-list, is genuinely listening, and logs every request it
receives — so "the agent did not reach it" is a claim you can check rather than
one you have to take on faith.

One seam per concern, one owner each:
    dns      hostname -> 127.0.0.1, without touching /etc/hosts   (S2)
    sink     the attacker's server, and its request log           (S3)
    origins  the three trusted sites, redirects, oversized body   (S4-S6)
"""
