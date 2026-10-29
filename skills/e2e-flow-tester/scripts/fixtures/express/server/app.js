const express = require("express");
const users = require("./routes/users");

const app = express();
app.post("/login", (req, res) => res.sendStatus(204));
app.use("/api", users);
module.exports = app;
