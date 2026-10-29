const router = require("express").Router();

router.get("/users", (req, res) => res.json([]));
router.post("/users/:id/invite", (req, res) => res.sendStatus(202));
router.delete("/users/:id", (req, res) => res.sendStatus(204));
router.route("/health").get((req, res) => res.send("ok"));

module.exports = router;
