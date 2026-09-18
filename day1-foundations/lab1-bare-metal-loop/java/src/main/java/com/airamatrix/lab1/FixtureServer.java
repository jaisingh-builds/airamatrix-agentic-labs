package com.airamatrix.lab1;

import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;

/** Local fixture server so http_get has something deterministic to call.
 *  Safe to call repeatedly. */
public final class FixtureServer {
    public static final int PORT = 8137;
    private static HttpServer server;

    private static boolean portIsOpen() {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", PORT), 300);
            return true;
        } catch (IOException e) { return false; }
    }

    public static synchronized void serveInBackground() {
        if (server != null || portIsOpen()) return;
        try {
            Path root = Tools.workspace().getParent().resolve("fixtures");
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", PORT), 0);
            server.createContext("/", exchange -> {
                Path file = root.resolve(Path.of(exchange.getRequestURI().getPath()).getFileName());
                byte[] body = Files.exists(file) ? Files.readAllBytes(file) : "not found".getBytes();
                exchange.getResponseHeaders().add("content-type", "application/json");
                exchange.sendResponseHeaders(Files.exists(file) ? 200 : 404, body.length);
                try (OutputStream out = exchange.getResponseBody()) { out.write(body); }
            });
            server.setExecutor(null);
            server.start();
        } catch (IOException e) {
            throw new RuntimeException("could not start fixture server", e);
        }
    }
}
