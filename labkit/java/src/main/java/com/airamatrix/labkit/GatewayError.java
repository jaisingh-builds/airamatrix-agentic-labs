package com.airamatrix.labkit;

public class GatewayError extends RuntimeException {
    public final int status;
    public GatewayError(int status, String body) {
        super("gateway returned " + status + ": "
              + body.substring(0, Math.min(400, body.length())));
        this.status = status;
    }
}
