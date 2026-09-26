package com.airamatrix.day4.lab53;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Map;

import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.ExitCodeGenerator;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Lab 5.3 as a Spring Boot command-line app (non-web). Same flags, outputs and exit codes as
 * lab5-3-pr-review/review.py, so {@code java -jar lab53.jar ...} replaces {@code python3 review.py ...}.
 *
 * <pre>
 *   java -jar lab53.jar --base day4 --head demo/apply-retry --repo /tmp/d4wt --out /tmp/rv/apply-retry
 *   java -jar lab53.jar demo-prs --base day4 --worktree /tmp/d4wt       # demo_prs.py
 * </pre>
 * Exit codes: 0 no blocking findings, 2 blocking findings, 1 could not review.
 */
@SpringBootApplication
public class Lab53Application implements CommandLineRunner, ExitCodeGenerator {

    private int exitCode;

    public static void main(String[] args) {
        System.exit(SpringApplication.exit(SpringApplication.run(Lab53Application.class, args)));
    }

    @Override
    public void run(String... args) {
        if (args.length > 0 && args[0].equals("demo-prs")) {
            PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8);
            PrintStream err = new PrintStream(new FileOutputStream(FileDescriptor.err), true, StandardCharsets.UTF_8);
            exitCode = new DemoPrs(Map.of(), out).run(Arrays.copyOfRange(args, 1, args.length), err);
        } else {
            exitCode = Review.defaults().run(args);
        }
    }

    @Override
    public int getExitCode() {
        return exitCode;
    }
}
