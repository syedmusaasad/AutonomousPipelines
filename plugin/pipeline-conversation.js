// Injects the owning conversation id into every shell the agent spawns so that
// pipeline launchers can key runs to the conversation (stable across resumes).
export const PipelineConversation = async () => ({
  "shell.env": async (input, output) => {
    if (input.sessionID) output.env.PIPELINE_CONVERSATION = input.sessionID;
  },
});
