package com.airamatrix.day4.lab52;

import java.io.FileOutputStream;
import java.io.FileDescriptor;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;

import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.ExitCodeGenerator;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Lab 5.2 in Java: the eval harness (run_evals.py) and the judge (judge.py) as one jar.
 *
 * <pre>
 *   java -jar lab52.jar [run_evals options]            # the eval gate - exit 0 pass, 1 fail, 2 could not run
 *   java -jar lab52.jar judge calibrate [--mode both]  # the LLM judge against human labels
 *   java -jar lab52.jar calibrate [--mode both]        # same (shortcut)
 * </pre>
 */
@SpringBootApplication
public class Lab52Application implements CommandLineRunner, ExitCodeGenerator {
    private int exitCode;

    public static void main(String[] args) {
        System.exit(SpringApplication.exit(SpringApplication.run(Lab52Application.class, args)));
    }

    @Override
    public void run(String... args) throws Exception {
        // The report has em dashes and middle dots, like the Python one: write UTF-8 whatever the console default is.
        PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8);
        PrintStream err = new PrintStream(new FileOutputStream(FileDescriptor.err), true, StandardCharsets.UTF_8);
        try {
            if (args.length > 0 && args[0].equals("judge")) {
                exitCode = Judge.main(args, 1, out, err);
            } else if (args.length > 0 && args[0].equals("calibrate")) {
                exitCode = Judge.main(args, 0, out, err);
            } else {
                exitCode = RunEvals.main(args, out, err, System.getenv(), RunEvals.liveSetup(System.getenv()));
            }
        } catch (java.io.IOException e) {       // a missing golden / results / calibration file
            err.println("setup: " + e);
            exitCode = 2;
        }
        out.flush();
        err.flush();
    }

    @Override
    public int getExitCode() {
        return exitCode;
    }
}
