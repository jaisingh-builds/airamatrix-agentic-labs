package com.airamatrix.capstone.runtime;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** The capstone responder on AgentCore Runtime: GET /ping, POST /invocations on 0.0.0.0:8080. */
@SpringBootApplication
public class RuntimeApplication {
    public static void main(String[] args) { SpringApplication.run(RuntimeApplication.class, args); }
}
