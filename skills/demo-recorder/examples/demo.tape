# Record: vhs examples/demo.tape
# Edit the steps below and re-run vhs after the tool changes.

Output assets/demo.gif
Require mytool

Set Shell "bash"
Set FontSize 22
Set Width 1200
Set Height 600
Set Padding 20
Set Framerate 24
Set TypingSpeed 50ms

# Demo for a CLI called "mytool". One task, about 20 seconds.
# Setup runs hidden: a throwaway HOME and a sample project, so no real config or tokens show up.
Hide
Type 'export HOME="$(mktemp -d)"'
Enter
Wait
Type "cp -r examples/sample-project /tmp/sample-project && cd /tmp/sample-project"
Enter
Wait
Type "clear"
Enter
Sleep 200ms
Show

Type "mytool --version"
Sleep 400ms
Enter
Wait
Sleep 2s

Type "mytool scan ."
Sleep 400ms
Enter
Wait
Sleep 2s

Sleep 1s
Type "mytool scan . --fix"
Sleep 400ms
Enter
Wait
Sleep 2s

# The payoff: the second scan is clean.
Type "mytool scan ."
Sleep 400ms
Enter
Wait
Sleep 2s

Sleep 3s
