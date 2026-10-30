from __future__ import annotations

from typing import Any

from analyst.completeness.models import (
    AgreementStatus,
    CompletenessReport,
    DraftParameter,
    MissingParameter,
    ParameterOrigin,
)
from analyst.completeness.rules import OPERATION_RULES, OperationRule
from pipeline_spec.models.enums import OperationType


class CompletenessChecker:
    """Deterministic validator for pipeline drafts against the MVP operation requirements."""

    def check_operation(
        self,
        step_id: str,
        step_name: str,
        operation_type: OperationType | str,
        params: dict[str, Any],
    ) -> list[MissingParameter]:
        """Checks whether an individual operation has all required parameters."""
        op_type = (
            OperationType(operation_type)
            if isinstance(operation_type, str)
            else operation_type
        )

        if op_type == OperationType.JOIN:
            return self._check_join_op(step_id, step_name, params)
        if op_type == OperationType.WITH_COLUMNS:
            return self._check_with_columns_op(step_id, step_name, params)
        if op_type == OperationType.AGGREGATE:
            return self._check_aggregate_op(step_id, step_name, params)

        missing: list[MissingParameter] = []
        rule = OPERATION_RULES.get(op_type)
        if not rule:
            return missing

        for req in rule.required_parameters:
            val = params.get(req.field_name)
            if self._is_empty(val):
                missing.append(
                    MissingParameter(
                        step_id=step_id,
                        step_name=step_name,
                        operation_type=op_type,
                        field_name=req.field_name,
                        question=req.question,
                        suggestion=req.default_suggestion,
                    )
                )

        return missing

    def check_draft(
        self,
        draft: dict[str, Any],
        parameters_metadata: list[DraftParameter] | None = None,
    ) -> CompletenessReport:
        """Comprehensive verification of the pipeline draft."""
        missing_params: list[MissingParameter] = []
        metadata_list = parameters_metadata or []

        # Separate user requirements from pending agent suggestions
        pending_suggestions: list[DraftParameter] = []
        confirmed_parameters: list[DraftParameter] = []

        for param in metadata_list:
            if (
                param.origin == ParameterOrigin.AGENT_SUGGESTION
                and param.status == AgreementStatus.PENDING
            ):
                pending_suggestions.append(param)
            elif param.status == AgreementStatus.AGREED or param.origin == ParameterOrigin.USER_EXPLICIT:
                confirmed_parameters.append(param)

        if not draft:
            return CompletenessReport(
                is_complete=False,
                missing_parameters=[],
                pending_suggestions=pending_suggestions,
                confirmed_parameters=confirmed_parameters,
                summary_message="Черновик спецификации еще не сформирован."
                if not pending_suggestions
                else f"Черновик не сформирован, ожидают согласования предложений аналитика: {len(pending_suggestions)}.",
            )

        # 1. Sources check
        sources = draft.get("sources", [])
        steps = draft.get("steps", [])
        has_read_op = any(
            self._get_op_type(s) == OperationType.READ for s in steps
        )
        if not sources and not has_read_op:
            missing_params.append(
                MissingParameter(
                    step_id="global_sources",
                    step_name="Определение источников данных",
                    operation_type=OperationType.READ,
                    field_name="sources",
                    question="Не указан ни один входной набор данных. Откуда конвейер должен читать данные?",
                    suggestion="Добавить источник 'raw_orders' в формате Parquet или CSV",
                )
            )

        # 2. Check each step's operation
        for idx, step in enumerate(steps, start=1):
            step_id = step.get("id", f"step_{idx}")
            step_name = step.get("name", step_id)
            op = step.get("operation", {})
            op_type_str = self._get_op_type(step)

            if not op_type_str:
                missing_params.append(
                    MissingParameter(
                        step_id=step_id,
                        step_name=step_name,
                        operation_type=OperationType.SELECT,
                        field_name="operation",
                        question=f"Для шага '{step_name}' не определен тип операции трансформации.",
                        suggestion="Указать операцию (filter, join, aggregate, with_columns и др.)",
                    )
                )
                continue

            try:
                op_type = OperationType(op_type_str)
            except ValueError:
                continue

            # Special deep validations
            if op_type == OperationType.JOIN:
                missing_params.extend(self._check_join_op(step_id, step_name, op))
            elif op_type == OperationType.WITH_COLUMNS:
                missing_params.extend(
                    self._check_with_columns_op(step_id, step_name, op)
                )
            elif op_type == OperationType.AGGREGATE:
                missing_params.extend(
                    self._check_aggregate_op(step_id, step_name, op)
                )
            else:
                missing_params.extend(
                    self.check_operation(step_id, step_name, op_type, op)
                )

        # 3. Sinks check
        sinks = draft.get("sinks", [])
        has_write_op = any(
            self._get_op_type(s) == OperationType.WRITE for s in steps
        )
        if not sinks:
            missing_params.append(
                MissingParameter(
                    step_id="global_sinks",
                    step_name="Определение целевого сохранения",
                    operation_type=OperationType.WRITE,
                    field_name="sinks",
                    question="Не указано целевое назначение для сохранения результатов. Куда выгружать данные?",
                    suggestion="Добавить целевую витрину 'daily_mart_sink' в формате Parquet в режиме overwrite",
                )
            )
        else:
            for idx, sink in enumerate(sinks, start=1):
                sink_id = sink.get("id", f"sink_{idx}")
                sink_name = sink.get("name", sink_id)
                if self._is_empty(sink.get("location")):
                    missing_params.append(
                        MissingParameter(
                            step_id=sink_id,
                            step_name=sink_name,
                            operation_type=OperationType.WRITE,
                            field_name="location",
                            question=f"Для целевой витрины '{sink_name}' не указан путь размещения данных (location).",
                            suggestion="s3://lake/gold/orders_mart/",
                        )
                    )
                # Check save_mode: must not be hallucinated without confirmation (A04)
                if self._is_empty(sink.get("save_mode")):
                    missing_params.append(
                        MissingParameter(
                            step_id=sink_id,
                            step_name=sink_name,
                            operation_type=OperationType.WRITE,
                            field_name="save_mode",
                            question=f"Для целевой витрины '{sink_name}' не указан режим сохранения (save_mode: overwrite, append).",
                            suggestion="overwrite",
                        )
                    )

        # 4. Determine overall completeness
        is_complete = len(missing_params) == 0 and len(pending_suggestions) == 0

        # Construct summary message
        if is_complete:
            summary = "Все обязательные параметры заполнены, предложения агента согласованы. Черновик полон."
        else:
            reasons = []
            if missing_params:
                reasons.append(f"не заполнено обязательных параметров: {len(missing_params)}")
            if pending_suggestions:
                reasons.append(
                    f"ожидают согласования предложений аналитика: {len(pending_suggestions)}"
                )
            summary = f"Черновик неполон: {', '.join(reasons)}."

        return CompletenessReport(
            is_complete=is_complete,
            missing_parameters=missing_params,
            pending_suggestions=pending_suggestions,
            confirmed_parameters=confirmed_parameters,
            summary_message=summary,
        )

    def _get_op_type(self, step: dict[str, Any]) -> str | None:
        op = step.get("operation")
        if isinstance(op, dict):
            return op.get("type")
        return None

    def _is_empty(self, val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, (str, list, dict)) and len(val) == 0:
            return True
        return False

    def _check_join_op(
        self, step_id: str, step_name: str, op: dict[str, Any]
    ) -> list[MissingParameter]:
        missing: list[MissingParameter] = []
        if self._is_empty(op.get("right_input")):
            missing.append(
                MissingParameter(
                    step_id=step_id,
                    step_name=step_name,
                    operation_type=OperationType.JOIN,
                    field_name="right_input",
                    question="С каким вторым датасетом производить объединение (Join)?",
                    suggestion="Указать ID второго датасета",
                )
            )
        if self._is_empty(op.get("on")):
            missing.append(
                MissingParameter(
                    step_id=step_id,
                    step_name=step_name,
                    operation_type=OperationType.JOIN,
                    field_name="on",
                    question="По какому ключу (колонкам) объединять таблицы (например: customer_id)?",
                    suggestion="['customer_id']",
                )
            )
        if self._is_empty(op.get("join_type")):
            missing.append(
                MissingParameter(
                    step_id=step_id,
                    step_name=step_name,
                    operation_type=OperationType.JOIN,
                    field_name="join_type",
                    question="Какой тип соединения использовать и как обрабатывать строки без пары (left — сохранить, inner — отбросить)?",
                    suggestion="left (сохранить все строки левой таблицы)",
                )
            )
        return missing

    def _check_with_columns_op(
        self, step_id: str, step_name: str, op: dict[str, Any]
    ) -> list[MissingParameter]:
        missing: list[MissingParameter] = []
        cols = op.get("columns", [])
        if self._is_empty(cols):
            missing.append(
                MissingParameter(
                    step_id=step_id,
                    step_name=step_name,
                    operation_type=OperationType.WITH_COLUMNS,
                    field_name="columns",
                    question="Какие новые колонки необходимо рассчитать и по каким формулам?",
                    suggestion="net_amount = amount * (1 - discount_pct / 100)",
                )
            )
        else:
            for c in cols:
                if isinstance(c, dict) and (self._is_empty(c.get("expr")) or self._is_empty(c.get("name"))):
                    missing.append(
                        MissingParameter(
                            step_id=step_id,
                            step_name=step_name,
                            operation_type=OperationType.WITH_COLUMNS,
                            field_name="columns.expr",
                            question=f"Не указана формула расчета для колонки '{c.get('name', 'unnamed')}'.",
                            suggestion="Задать формулу выражения",
                        )
                    )
        return missing

    def _check_aggregate_op(
        self, step_id: str, step_name: str, op: dict[str, Any]
    ) -> list[MissingParameter]:
        missing: list[MissingParameter] = []
        is_global = op.get("is_global") is True or op.get("global_aggregation") is True
        if not is_global and self._is_empty(op.get("group_by")):
            missing.append(
                MissingParameter(
                    step_id=step_id,
                    step_name=step_name,
                    operation_type=OperationType.AGGREGATE,
                    field_name="group_by",
                    question="По каким колонкам/разрезам производить группировку данных?",
                    suggestion="['order_date', 'country']",
                )
            )
        aggs = op.get("aggregations", [])
        if self._is_empty(aggs):
            missing.append(
                MissingParameter(
                    step_id=step_id,
                    step_name=step_name,
                    operation_type=OperationType.AGGREGATE,
                    field_name="aggregations",
                    question="Какие агрегатные функции применить к сгруппированным данным (например: sum, count, avg)?",
                    suggestion="count(order_id) и sum(net_amount)",
                )
            )
        return missing
