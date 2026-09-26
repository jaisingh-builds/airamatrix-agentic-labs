package com.airamatrix.agentcore;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * The AiraMatrix ops agents, in Java, for AgentCore Runtime. One image, three runtimes:
 * the ROLE environment variable picks investigator, reviewer or supervisor - the same design
 * as the Python agents in ../06-agents/agent/main.py.
 */
@SpringBootApplication
public class AgentApplication {
    public static void main(String[] args) {
        SpringApplication.run(AgentApplication.class, args);
    }
}
