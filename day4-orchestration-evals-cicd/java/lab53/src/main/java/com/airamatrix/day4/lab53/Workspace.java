package com.airamatrix.day4.lab53;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/**
 * The workspace the model's context is read from: a copy of the tree at a revision, with no .git,
 * no secret-bearing files, and every secret pattern masked in every text file.
 * Port of review.py's sanitized_workspace(). {@code git archive} gives a tar; the JDK has no tar
 * reader, so {@link #extractTar} reads the few entry types git writes (files, dirs, pax headers).
 */
record Workspace(Path dir, int masked, int dropped) {

    static final Pattern DROP_FILES = Pattern.compile(
            "(^|/)(\\.env[^/]*|[^/]*\\.(pem|key|p12|pfx)|[^/]*credentials[^/]*|\\.npmrc|\\.pypirc)$",
            Pattern.CASE_INSENSITIVE);

    /** A copy of the tree at {@code rev}: no .git, no secret-bearing files, secrets masked. */
    static Workspace sanitized(Path repo, String rev) throws IOException {
        Path out = Files.createTempDirectory("review-ws-");
        try {
            if (isGitRepo(repo)) {
                // Inside a repository a failed archive (bad rev, broken repo, timeout) is an error: falling back
                // to a copy of the working tree would review something other than `rev` with a normal verdict.
                Proc.Result r = Proc.run(List.of("git", "archive", "--format=tar", rev), repo, null, 300);
                if (r.code() != 0) {
                    String err = r.err().strip();
                    throw new IOException("git archive " + rev + " failed (exit " + r.code() + "): "
                            + (err.length() > 300 ? err.substring(0, 300) : err));
                }
                extractTar(r.out(), out);
            } else {
                copyTree(repo, out);                              // git missing, or not a repo: copy the tree
            }
            int masked = 0, dropped = 0;
            List<Path> files;
            try (Stream<Path> s = Files.walk(out)) {
                files = s.filter(p -> Files.isRegularFile(p, LinkOption.NOFOLLOW_LINKS)).sorted().toList();
            }
            for (Path f : files) {
                String rel = out.relativize(f).toString().replace('\\', '/');
                if (DROP_FILES.matcher(rel).find()) {
                    Files.delete(f);
                    dropped++;
                    continue;
                }
                String text = readUtf8(f);
                if (text == null) continue;                        // binary: nothing to grep for anyway
                String clean = Review.maskAll(text);
                if (!clean.equals(text)) {
                    Files.writeString(f, clean, StandardCharsets.UTF_8);
                    masked++;
                }
            }
            return new Workspace(out, masked, dropped);
        } catch (IOException | RuntimeException e) {
            deleteRecursively(out);
            throw e;
        }
    }

    /** review.py's _is_git_repo(): false when git is not installed or the folder is not a repository. */
    static boolean isGitRepo(Path repo) {
        try {
            return Proc.run(List.of("git", "rev-parse", "--git-dir"), repo, null, 60).code() == 0;
        } catch (IOException gitMissing) {
            return false;
        }
    }

    /** Strict UTF-8, like Python's read_text(encoding="utf-8"): null if the file is not text. */
    static String readUtf8(Path f) {
        try {
            byte[] b = Files.readAllBytes(f);
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(b)).toString();
        } catch (CharacterCodingException e) {
            return null;
        } catch (IOException e) {
            return null;
        }
    }

    static void copyTree(Path src, Path dst) throws IOException {
        try (Stream<Path> s = Files.walk(src)) {
            for (Path p : (Iterable<Path>) s::iterator) {
                Path rel = src.relativize(p);
                if (rel.toString().isEmpty()) continue;
                boolean inGit = false;
                for (Path part : rel) if (part.toString().equals(".git")) inGit = true;
                if (inGit) continue;
                if (Files.isSymbolicLink(p)) continue;               // never follow a link out of the tree
                Path t = dst.resolve(rel.toString());
                if (Files.isDirectory(p)) Files.createDirectories(t);
                else if (Files.isRegularFile(p)) {
                    Files.createDirectories(t.getParent());
                    Files.copy(p, t);
                }
            }
        }
    }

    static void deleteRecursively(Path dir) {
        if (dir == null || !Files.exists(dir, LinkOption.NOFOLLOW_LINKS)) return;
        try (Stream<Path> s = Files.walk(dir)) {
            for (Path p : s.sorted(Comparator.reverseOrder()).toList()) {
                try { Files.deleteIfExists(p); } catch (IOException ignored) { /* best effort */ }
            }
        } catch (IOException ignored) { /* best effort, like shutil.rmtree(ignore_errors=True) */ }
    }

    // ------------------------------------------------------------------ a minimal tar reader
    /**
     * Extract the regular files and directories of a tar (as written by git archive) under dest.
     * Links are skipped; an entry that would land outside dest is refused (tarfile's "data" filter).
     */
    static void extractTar(byte[] tar, Path dest) throws IOException {
        Path root = dest.toAbsolutePath().normalize();
        int pos = 0;
        String paxPath = null, longName = null;
        while (pos + 512 <= tar.length) {
            if (allZero(tar, pos, 512)) break;
            String name = cstr(tar, pos, 100);
            String magic = cstr(tar, pos + 257, 6);
            String prefix = magic.startsWith("ustar") ? cstr(tar, pos + 345, 155) : "";
            long size = octal(tar, pos + 124, 12);
            char type = (char) tar[pos + 156];
            int data = pos + 512;
            if (data + size > tar.length) throw new IOException("truncated tar entry " + name);
            pos = data + (int) ((size + 511) / 512 * 512);
            switch (type) {
                case 'g' -> { /* pax global header: the commit id git archive records */ }
                case 'x' -> paxPath = paxValue(tar, data, (int) size, "path");
                case 'L' -> longName = cstr(tar, data, (int) size);
                default -> {
                    String full = paxPath != null ? paxPath : longName != null ? longName
                            : prefix.isEmpty() ? name : prefix + "/" + name;
                    paxPath = null;
                    longName = null;
                    if (type != '0' && type != '\0' && type != '7' && type != '5') break;   // links, devices
                    Path target = root.resolve(full).normalize();
                    if (!target.startsWith(root) || full.startsWith("/")) {
                        throw new IOException("unsafe path in archive: " + full);
                    }
                    if (type == '5') {
                        Files.createDirectories(target);
                    } else {
                        Files.createDirectories(target.getParent());
                        Files.write(target, java.util.Arrays.copyOfRange(tar, data, data + (int) size));
                    }
                }
            }
        }
    }

    private static boolean allZero(byte[] b, int off, int len) {
        for (int i = off; i < off + len; i++) if (b[i] != 0) return false;
        return true;
    }

    private static String cstr(byte[] b, int off, int len) {
        int end = off;
        while (end < off + len && end < b.length && b[end] != 0) end++;
        return new String(b, off, end - off, StandardCharsets.UTF_8);
    }

    private static long octal(byte[] b, int off, int len) {
        String s = cstr(b, off, len).trim();
        return s.isEmpty() ? 0 : Long.parseLong(s, 8);
    }

    /** pax records: "LEN key=value\n" repeated. */
    private static String paxValue(byte[] b, int off, int len, String key) {
        String found = null;
        int i = off, end = off + len;
        while (i < end) {
            int sp = i;
            while (sp < end && b[sp] != ' ') sp++;
            if (sp >= end) break;
            int recLen;
            try { recLen = Integer.parseInt(new String(b, i, sp - i, StandardCharsets.US_ASCII)); }
            catch (NumberFormatException e) { break; }
            if (recLen <= 0 || i + recLen > end) break;
            String rec = new String(b, sp + 1, i + recLen - sp - 1, StandardCharsets.UTF_8);
            if (rec.endsWith("\n")) rec = rec.substring(0, rec.length() - 1);
            int eq = rec.indexOf('=');
            if (eq > 0 && rec.substring(0, eq).equals(key)) found = rec.substring(eq + 1);
            i += recLen;
        }
        return found;
    }
}
