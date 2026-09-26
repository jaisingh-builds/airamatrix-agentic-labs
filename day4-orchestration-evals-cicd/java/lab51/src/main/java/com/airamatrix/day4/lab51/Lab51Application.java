package com.airamatrix.day4.lab51;

import java.util.List;

import org.springframework.boot.CommandLineRunner;
import org.springframework.boot.ExitCodeGenerator;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.condition.ConditionalOnNotWebApplication;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.beans.factory.annotation.Value;

/**
 * Lab 5.1 in Java / Spring Boot.
 *
 * <pre>
 *   java -jar lab51.jar run|show|approve|reject|apply|resume|list|replay|tokens ...   the CLI (no web server)
 *   java -jar lab51.jar serve              REST API for the agents side, 127.0.0.1:8170 (holds the READ token)
 *   java -jar lab51.jar serve --apply      REST API for the write side,  127.0.0.1:8171 (holds the APPLY token)
 * </pre>
 *
 * The store is plain JDBC on SQLite (see {@link Store}), so Spring's DataSource auto-configuration is off.
 */
@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)
public class Lab51Application {

    public static void main(String[] args) {
        boolean serve = args.length > 0 && args[0].equals("serve");
        SpringApplicationBuilder app = new SpringApplicationBuilder(Lab51Application.class)
                .addCommandLineProperties(false);          // CLI flags are ours, not Spring properties
        if (!serve) {
            app.web(WebApplicationType.NONE).bannerMode(org.springframework.boot.Banner.Mode.OFF).logStartupInfo(false)
                    .properties("logging.level.root=WARN");
            ConfigurableApplicationContext ctx = app.run(args);
            System.exit(SpringApplication.exit(ctx));
            return;
        }
        boolean applyMode = List.of(args).contains("--apply");
        int port = applyMode ? 8171 : 8170;
        for (int i = 1; i < args.length - 1; i++) if (args[i].equals("--port")) port = Integer.parseInt(args[i + 1]);
        String refusal = ServeSettings.startupRefusal(applyMode, System::getenv);
        if (refusal != null) {
            System.err.println(refusal);
            System.exit(1);
        }
        app.web(WebApplicationType.SERVLET)
                .properties("server.port=" + port, "server.address=127.0.0.1",
                        "lab51.serve.mode=" + (applyMode ? "apply" : "agents"))
                .run(args);
    }

    // ---- CLI mode: one command, then exit with its code
    @Bean
    @ConditionalOnNotWebApplication
    CliRunner cliRunner() {
        return new CliRunner();
    }

    static class CliRunner implements CommandLineRunner, ExitCodeGenerator {
        private int code;
        @Override public void run(String... args) {
            code = new Cli(System.out, System.err, System::getenv, Cli.gatewayRunners()).main(args);
        }
        @Override public int getExitCode() { return code; }
    }

    // ---- serve mode
    @Bean
    @ConditionalOnWebApplication
    ServeSettings serveSettings(@Value("${lab51.serve.mode:agents}") String mode) {
        return ServeSettings.fromEnv("apply".equals(mode), System::getenv);
    }

    @Bean(destroyMethod = "close")
    @ConditionalOnWebApplication
    Store store() {
        return new Store(Cli.dbPath(System::getenv));
    }

    @Bean
    @ConditionalOnWebApplication
    Cli.RunnerFactory runnerFactory() {
        return Cli.gatewayRunners();
    }
}
