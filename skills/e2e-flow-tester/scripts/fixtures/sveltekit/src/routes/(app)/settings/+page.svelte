<script>
  import { enhance } from "$app/forms";
</script>

<form method="POST" use:enhance>
  <label>Display name <input name="name" /></label>
  <button>Save changes</button>
</form>
