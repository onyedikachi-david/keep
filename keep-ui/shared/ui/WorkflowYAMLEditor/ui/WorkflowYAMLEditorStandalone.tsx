import type { editor } from "monaco-editor";
import { WorkflowYAMLEditor } from "./WorkflowYAMLEditor";
import { useCallback, useEffect, useRef, useState } from "react";
import { DefinitionV2 } from "@/entities/workflows";
import { wrapDefinitionV2 } from "@/entities/workflows/lib/parser";
import { parseWorkflow } from "@/entities/workflows/lib/parser";
import { useProviders } from "@/utils/hooks/useProviders";
import { useWorkflowActions } from "@/entities/workflows/model/useWorkflowActions";
import { WorkflowYamlEditorHeader } from "./WorkflowYamlEditorHeader";
import { getOrderedWorkflowYamlString } from "@/entities/workflows/lib/yaml-utils";
import { YamlValidationError } from "../model/types";
import { Button } from "@tremor/react";
import { WorkflowTestRunButton } from "@/features/workflows/test-run";

export function WorkflowYAMLEditorStandalone({
  workflowId,
  yamlString,
  "data-testid": dataTestId = "wf-yaml-standalone-editor",
}: {
  workflowId: string;
  yamlString: string;
  "data-testid"?: string;
}) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);
  const [validationErrors, setValidationErrors] = useState<
    YamlValidationError[]
  >([]);
  const [isEditorMounted, setIsEditorMounted] = useState(false);
  const [lastDeployedAt, setLastDeployedAt] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [originalContent, setOriginalContent] = useState("");
  const [definition, setDefinition] = useState<DefinitionV2 | null>(null);

  const isValid = validationErrors?.length === 0;

  const { updateWorkflow } = useWorkflowActions();
  const { data: { providers } = {} } = useProviders();

  const parseYamlToDefinition = useCallback(
    (yamlString: string) => {
      try {
        setDefinition(
          wrapDefinitionV2({
            ...parseWorkflow(yamlString, providers ?? []),
            // isValid is not used in the standalone editor, so we set it to true
            isValid: true,
          })
        );
      } catch (error) {
        console.error("Failed to parse YAML:", error);
      }
    },
    [providers]
  );

  const handleContentChange = (value: string | undefined) => {
    if (!value) {
      return;
    }
    setHasChanges(value !== originalContent);
    parseYamlToDefinition(value);
  };

  useEffect(() => {
    setOriginalContent(getOrderedWorkflowYamlString(yamlString));
  }, [yamlString]);

  const handleSaveWorkflow = async () => {
    if (!editorRef.current) {
      return;
    }
    if (!workflowId) {
      console.error("Workflow ID is required to save the workflow");
      return;
    }
    setIsSaving(true);
    const content = editorRef.current.getValue();
    try {
      // sending the yaml string to the backend
      // TODO: validate the yaml content and show useful (inline) errors
      await updateWorkflow(workflowId, content);

      setOriginalContent(content);
      setHasChanges(false);
    } catch (err) {
      console.error("Failed to save workflow:", err);
    } finally {
      setLastDeployedAt(Date.now());
      setIsSaving(false);
    }
  };

  const handleEditorDidMount = (
    editor: editor.IStandaloneCodeEditor,
    monacoInstance: typeof import("monaco-editor")
  ) => {
    editorRef.current = editor;
    monacoRef.current = monacoInstance;

    const model = editor?.getModel();
    if (model) {
      parseYamlToDefinition(model.getValue());
    }

    setIsEditorMounted(true);
  };

  return (
    <div className="w-full h-full flex flex-col relative">
      <WorkflowYamlEditorHeader
        workflowId={workflowId}
        isInitialized={isEditorMounted}
        lastDeployedAt={lastDeployedAt}
        hasChanges={hasChanges}
      >
        <WorkflowTestRunButton
          workflowId={workflowId}
          definition={definition}
          isValid={isValid}
          data-testid="wf-yaml-editor-test-run-button"
        />
        <Button
          color="orange"
          size="sm"
          className="min-w-28 relative disabled:opacity-70"
          disabled={!hasChanges || isSaving}
          onClick={handleSaveWorkflow}
          data-testid="wf-yaml-editor-save-button"
        >
          {isSaving ? "Saving..." : "Save"}
        </Button>
      </WorkflowYamlEditorHeader>
      <WorkflowYAMLEditor
        workflowYamlString={yamlString}
        filename={workflowId ?? "workflow"}
        workflowId={workflowId}
        onMount={handleEditorDidMount}
        onChange={handleContentChange}
        onValidationErrors={setValidationErrors}
        data-testid={dataTestId}
      />
    </div>
  );
}
