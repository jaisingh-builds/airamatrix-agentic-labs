package com.airamatrix.day4.common;

import java.util.List;

import com.fasterxml.jackson.databind.JsonNode;

/** One Messages API call. labkit's GatewayClient::messages fits; tests pass a scripted fake. */
@FunctionalInterface
public interface ModelClient {
    JsonNode messages(List<?> messages, List<?> tools, String system, int maxTokens);
}
