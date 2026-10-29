// Browser code, not a server: axios calls must not be listed as endpoints.
import axios from "axios";
export const load = () => axios.get("/users");
