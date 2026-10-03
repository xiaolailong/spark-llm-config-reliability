#!/usr/bin/env Rscript

# 80_formal_statistics_v012.R
#
# Formal inferential statistics for the SEALED Formal90 master dataset.
# V0.1.2 changes ONLY an input-path typo introduced in V0.1.1, while retaining
# the LRT df reporting fix. No statistical method, seed, model, data, or scoring changed.
# No model calls. No scoring changes. No task changes.
#
# Primary analyses:
#   - Wilson 95% CI
#   - paired C0/C1 transitions
#   - exact McNemar + Holm
#   - A-F stratified task-level paired bootstrap (10,000 replicates)
#   - Initial success GLMM:
#       initial_success ~ model * condition + category + (1 | task_id)
#   - pre-specified fallback: task-clustered binomial GEE
#   - repair recovery analyzed conditional on Initial failure
#
# Requires: lme4, emmeans
# GEE fallback additionally requires: geepack

options(stringsAsFactors = FALSE, warn = 1)

get_script_path <- function() {
  args <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", args, value = TRUE)
  if (length(hit) > 0) {
    return(normalizePath(sub("^--file=", "", hit[1]), winslash = "/", mustWork = TRUE))
  }
  # RStudio/source fallback
  frames <- sys.frames()
  for (i in rev(seq_along(frames))) {
    of <- frames[[i]]$ofile
    if (!is.null(of)) return(normalizePath(of, winslash = "/", mustWork = TRUE))
  }
  stop("Cannot determine script path. Run with Rscript or source this file.")
}

script_path <- get_script_path()
script_dir <- dirname(script_path)
# <root>/paper_core/04_analysis/scripts
project_root <- normalizePath(file.path(script_dir, "..", "..", ".."),
                              winslash = "/", mustWork = TRUE)
master_path <- file.path(project_root, "paper_core", "04_analysis",
                         "formal_master_v01", "formal_master_540_v01.csv")
out_dir <- file.path(project_root, "paper_core", "04_analysis",
                     "formal_statistics_v012")
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

log_path <- file.path(out_dir, "formal_statistics_console_v012.txt")
zz <- file(log_path, open = "wt", encoding = "UTF-8")
sink(zz, type = "output", split = TRUE)
sink(zz, type = "message", append = TRUE)
on.exit({
  try(sink(type = "message"), silent = TRUE)
  try(sink(type = "output"), silent = TRUE)
  try(close(zz), silent = TRUE)
}, add = TRUE)

cat("Formal statistics V0.1.2\n")
cat("Project root:", project_root, "\n")
cat("Input:", master_path, "\n\n")

needed <- c("lme4", "emmeans")
missing <- needed[!vapply(needed, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing) > 0) {
  writeLines(missing, file.path(out_dir, "missing_R_packages_v012.txt"))
  stop("Missing required R packages: ", paste(missing, collapse = ", "),
       ". Install them before rerunning.")
}

# -----------------------
# Helpers
# -----------------------
sha256_file <- function(path) {
  # tools::md5sum is not SHA256; use system sha256sum available on Ubuntu.
  ans <- suppressWarnings(system2("sha256sum", shQuote(path), stdout = TRUE, stderr = TRUE))
  if (length(ans) < 1 || !grepl("^[0-9a-fA-F]{64}", ans[1])) {
    return(NA_character_)
  }
  sub("\\s+.*$", "", ans[1])
}

wilson_ci <- function(x, n, conf = 0.95) {
  if (n <= 0) return(c(NA_real_, NA_real_))
  z <- qnorm(1 - (1-conf)/2)
  phat <- x/n
  den <- 1 + z^2/n
  center <- (phat + z^2/(2*n))/den
  half <- z * sqrt((phat*(1-phat)/n) + z^2/(4*n^2))/den
  c(max(0, center-half), min(1, center+half))
}

prop_row <- function(model, condition, x, n, type) {
  ci <- wilson_ci(x,n)
  data.frame(
    analysis = type,
    model = model,
    condition = condition,
    success = x,
    n = n,
    proportion = x/n,
    percent = 100*x/n,
    ci_low = ci[1],
    ci_high = ci[2],
    ci_low_percent = 100*ci[1],
    ci_high_percent = 100*ci[2],
    stringsAsFactors = FALSE
  )
}

fmt_num <- function(x, digits=6) {
  ifelse(is.na(x), "", formatC(x, digits=digits, format="fg", flag="#"))
}

# -----------------------
# Load + normalize
# -----------------------
dat <- read.csv(master_path, check.names = FALSE, na.strings = c("", "NA"),
                stringsAsFactors = FALSE, fileEncoding = "UTF-8-BOM")

model_map <- c(qwen="Qwen", deepseek="DeepSeek", gpt6_sol="GPT")
condition_map <- c(
  C0_closed_book="C0",
  C1_frozen_official_docs_v04="C1",
  C0="C0", C1="C1"
)
category_map <- c(
  A_resource="A", B_driver_executor="B", C_yarn_deploy="C",
  D_source_precedence="D", E_dependency_interaction="E", F_integrated="F",
  A="A", B="B", C="C", D="D", E="E", F="F"
)

dat$model_short <- unname(model_map[dat$model])
dat$condition_short <- unname(condition_map[dat$condition])
dat$category_short <- unname(category_map[dat$category])

boolify <- function(x) {
  if (is.logical(x)) return(x)
  y <- tolower(trimws(as.character(x)))
  z <- rep(NA, length(y))
  z[y %in% c("true","1","yes","y","pass","success","succeeded")] <- TRUE
  z[y %in% c("false","0","no","n","fail","failure","failed")] <- FALSE
  z
}
dat$initial_success_b <- boolify(dat$initial_success)
dat$repair_eligible_b <- boolify(dat$repair_eligible)
dat$repair_success_b <- boolify(dat$repair_success)
dat$final_success_b <- boolify(dat$final_after_one_repair_success)

# -----------------------
# Fail-fast authoritative checks
# -----------------------
stopifnot(
  nrow(dat) == 540,
  length(unique(dat$task_id)) == 90,
  all(!is.na(dat$model_short)),
  all(!is.na(dat$condition_short)),
  all(!is.na(dat$category_short)),
  all(!is.na(dat$initial_success_b)),
  all(!is.na(dat$repair_eligible_b)),
  all(!is.na(dat$final_success_b)),
  !anyDuplicated(dat[, c("model_short","condition_short","task_id")])
)

if (!all(table(dat$task_id) == 6)) stop("Each task_id must have exactly 6 rows.")
if (!identical(as.integer(table(factor(unique(dat[,c("task_id","category_short")])$category_short,
                                             levels=LETTERS[1:6]))),
               rep(15L,6))) {
  stop("A-F category structure is not 15 tasks per category.")
}
if (!all(dat$repair_eligible_b == !dat$initial_success_b)) {
  stop("repair_eligible is inconsistent with Initial failure.")
}
expected_final <- dat$initial_success_b |
  (dat$repair_eligible_b & ifelse(is.na(dat$repair_success_b), FALSE, dat$repair_success_b))
if (!all(dat$final_success_b == expected_final)) {
  stop("final_after_one_repair_success logic mismatch.")
}

expected_initial <- data.frame(
  model=c("Qwen","Qwen","DeepSeek","DeepSeek","GPT","GPT"),
  condition=rep(c("C0","C1"),3),
  success=c(23,61,65,79,70,68), n=90
)
for (i in seq_len(nrow(expected_initial))) {
  z <- subset(dat, model_short==expected_initial$model[i] &
                   condition_short==expected_initial$condition[i])
  if (sum(z$initial_success_b) != expected_initial$success[i] || nrow(z)!=90) {
    stop("Authoritative Initial count mismatch: ",
         expected_initial$model[i], "/", expected_initial$condition[i])
  }
}

cat("Authoritative fail-fast checks: PASS\n")
cat("Rows=540, tasks=90, six fully crossed observations/task.\n\n")

# -----------------------
# 1. Initial Wilson intervals
# -----------------------
initial_rows <- list()
k <- 1
for (m in c("Qwen","DeepSeek","GPT")) {
  for (cnd in c("C0","C1")) {
    z <- subset(dat, model_short==m & condition_short==cnd)
    initial_rows[[k]] <- prop_row(m,cnd,sum(z$initial_success_b),nrow(z),"Initial")
    k <- k+1
  }
}
initial_wilson <- do.call(rbind, initial_rows)
write.csv(initial_wilson, file.path(out_dir,"01_initial_wilson_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 2. Paired transitions + exact McNemar
# -----------------------
paired <- list()
k <- 1
for (m in c("Qwen","DeepSeek","GPT")) {
  z <- subset(dat, model_short==m,
              select=c("task_id","condition_short","initial_success_b"))
  w <- reshape(z, idvar="task_id", timevar="condition_short", direction="wide")
  names(w) <- sub("^initial_success_b\\.", "", names(w))
  pp <- sum(w$C0 & w$C1)
  fp <- sum(!w$C0 & w$C1)  # FAIL -> PASS
  pf <- sum(w$C0 & !w$C1)  # PASS -> FAIL
  ff <- sum(!w$C0 & !w$C1)
  p_exact <- binom.test(fp, fp+pf, p=0.5, alternative="two.sided")$p.value
  paired[[k]] <- data.frame(
    model=m,
    pass_to_pass=pp,
    fail_to_pass=fp,
    pass_to_fail=pf,
    fail_to_fail=ff,
    discordant=fp+pf,
    c0_success=pp+pf,
    c1_success=pp+fp,
    risk_difference=(fp-pf)/90,
    risk_difference_pp=100*(fp-pf)/90,
    exact_mcnemar_p=p_exact,
    stringsAsFactors=FALSE
  )
  k <- k+1
}
paired_df <- do.call(rbind,paired)
paired_df$holm_p <- p.adjust(paired_df$exact_mcnemar_p, method="holm")
write.csv(paired_df, file.path(out_dir,"02_paired_transitions_exact_mcnemar_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 3. Stratified task-level paired bootstrap
# -----------------------
set.seed(20261001)
B <- 10000L
task_meta <- unique(dat[,c("task_id","category_short")])
task_meta <- task_meta[order(task_meta$category_short, task_meta$task_id),]
n_tasks <- nrow(task_meta)
mods <- c("Qwen","DeepSeek","GPT")
conds <- c("C0","C1")
arr <- array(NA_real_, dim=c(n_tasks,length(mods),length(conds)),
             dimnames=list(task_meta$task_id,mods,conds))

for (i in seq_len(n_tasks)) {
  tid <- task_meta$task_id[i]
  for (j in seq_along(mods)) {
    for (q in seq_along(conds)) {
      zz <- subset(dat, task_id==tid & model_short==mods[j] &
                        condition_short==conds[q])
      if (nrow(zz)!=1) stop("Bootstrap matrix construction failed.")
      arr[i,j,q] <- as.numeric(zz$initial_success_b)
    }
  }
}
cat_indices <- split(seq_len(n_tasks), task_meta$category_short)

boot <- matrix(NA_real_, nrow=B, ncol=length(mods),
               dimnames=list(NULL,mods))
for (r in seq_len(B)) {
  idx <- unlist(lapply(cat_indices, function(ix) sample(ix,length(ix),replace=TRUE)),
                use.names=FALSE)
  for (j in seq_along(mods)) {
    boot[r,j] <- mean(arr[idx,j,"C1"] - arr[idx,j,"C0"])
  }
}

boot_summary <- data.frame()
for (m in mods) {
  point <- paired_df$risk_difference[paired_df$model==m]
  qs <- quantile(boot[,m], probs=c(.025,.975), names=FALSE, type=6)
  boot_summary <- rbind(boot_summary, data.frame(
    model=m,
    bootstrap_replicates=B,
    stratified_by_category=TRUE,
    point_risk_difference=point,
    point_difference_pp=100*point,
    percentile_ci_low=qs[1],
    percentile_ci_high=qs[2],
    percentile_ci_low_pp=100*qs[1],
    percentile_ci_high_pp=100*qs[2],
    stringsAsFactors=FALSE
  ))
}
write.csv(boot_summary, file.path(out_dir,"03_bootstrap_c1_minus_c0_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")
boot_long <- data.frame(
  replicate=rep(seq_len(B), times=length(mods)),
  model=rep(mods, each=B),
  risk_difference=as.vector(boot)
)
write.csv(boot_long, file.path(out_dir,"03b_bootstrap_replicates_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 4. Repair conditional recovery Wilson intervals
# -----------------------
repair_rows <- list(); k <- 1
for (m in mods) {
  for (cnd in conds) {
    z <- subset(dat, model_short==m & condition_short==cnd & repair_eligible_b)
    x <- sum(z$repair_success_b, na.rm=TRUE)
    repair_rows[[k]] <- prop_row(m,cnd,x,nrow(z),"Repair recovery | Initial failure")
    k <- k+1
  }
}
repair_wilson <- do.call(rbind,repair_rows)

# Overall within model as an additional descriptive line
for (m in mods) {
  z <- subset(dat, model_short==m & repair_eligible_b)
  x <- sum(z$repair_success_b, na.rm=TRUE)
  repair_wilson <- rbind(repair_wilson,
                         prop_row(m,"Overall",x,nrow(z),
                                  "Repair recovery | Initial failure"))
}
write.csv(repair_wilson, file.path(out_dir,"04_repair_recovery_wilson_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 5. Final-after-one-repair Wilson intervals
# -----------------------
final_rows <- list(); k <- 1
for (m in mods) {
  for (cnd in conds) {
    z <- subset(dat, model_short==m & condition_short==cnd)
    final_rows[[k]] <- prop_row(m,cnd,sum(z$final_success_b),nrow(z),
                                "Final after one repair")
    k <- k+1
  }
}
final_wilson <- do.call(rbind,final_rows)
write.csv(final_wilson, file.path(out_dir,"05_final_wilson_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 6. A-F category descriptive table
# -----------------------
category_out <- data.frame()
for (m in mods) {
  for (cnd in conds) {
    for (ca in LETTERS[1:6]) {
      z <- subset(dat, model_short==m & condition_short==cnd & category_short==ca)
      ci <- wilson_ci(sum(z$initial_success_b),nrow(z))
      category_out <- rbind(category_out,data.frame(
        model=m, condition=cnd, category=ca,
        success=sum(z$initial_success_b), n=nrow(z),
        percent=100*mean(z$initial_success_b),
        wilson_low_percent=100*ci[1], wilson_high_percent=100*ci[2],
        stringsAsFactors=FALSE
      ))
    }
  }
}
write.csv(category_out, file.path(out_dir,"06_category_initial_descriptive_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 7. Initial failure layers
# -----------------------
failure_rows <- subset(dat, !initial_success_b)
failure_layers <- as.data.frame(
  xtabs(~ model_short + condition_short + initial_failure_layer,
        data=failure_rows),
  stringsAsFactors=FALSE
)
names(failure_layers) <- c("model","condition","failure_layer","cases")
failure_layers <- subset(failure_layers,cases>0)
write.csv(failure_layers, file.path(out_dir,"07_initial_failure_layers_v012.csv"),
          row.names=FALSE, fileEncoding="UTF-8")

# -----------------------
# 8. GLMM
# -----------------------
gdat <- dat
gdat$initial_success_num <- as.integer(gdat$initial_success_b)
gdat$model_f <- factor(gdat$model_short, levels=c("Qwen","DeepSeek","GPT"))
gdat$condition_f <- factor(gdat$condition_short, levels=c("C0","C1"))
gdat$category_f <- factor(gdat$category_short, levels=LETTERS[1:6])
gdat$task_f <- factor(gdat$task_id)

full_formula <- initial_success_num ~ model_f * condition_f + category_f + (1 | task_f)
reduced_formula <- initial_success_num ~ model_f + condition_f + category_f + (1 | task_f)

ctrl <- lme4::glmerControl(optimizer="bobyqa",
                           optCtrl=list(maxfun=200000),
                           check.conv.singular="ignore")
glmm <- lme4::glmer(full_formula, data=gdat, family=binomial(link="logit"),
                    control=ctrl)
glmm_reduced <- lme4::glmer(reduced_formula, data=gdat, family=binomial(link="logit"),
                            control=ctrl)

conv_msgs <- glmm@optinfo$conv$lme4$messages
converged <- is.null(conv_msgs)
singular <- lme4::isSingular(glmm, tol=1e-4)
co <- summary(glmm)$coefficients
separation_suspected <- any(!is.finite(co)) ||
  any(abs(co[,1]) > 20, na.rm=TRUE) ||
  any(co[,2] > 10, na.rm=TRUE)

glmm_ok <- converged && !singular && !separation_suspected

diag <- data.frame(
  model="GLMM",
  converged=converged,
  singular=singular,
  separation_suspected=separation_suspected,
  accepted_as_primary=glmm_ok,
  convergence_message=ifelse(is.null(conv_msgs),"",paste(conv_msgs,collapse=" | ")),
  stringsAsFactors=FALSE
)
write.csv(diag,file.path(out_dir,"08_glmm_diagnostics_v012.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

capture.output(summary(glmm),
               file=file.path(out_dir,"08b_glmm_summary_v012.txt"))
saveRDS(glmm,file.path(out_dir,"08c_glmm_model_v012.rds"))

fixed <- data.frame(
  term=rownames(co),
  estimate_log_odds=co[,1],
  std_error=co[,2],
  z_value=co[,3],
  p_value=co[,4],
  odds_ratio=exp(co[,1]),
  ci_low_or=exp(co[,1]-1.96*co[,2]),
  ci_high_or=exp(co[,1]+1.96*co[,2]),
  row.names=NULL,
  stringsAsFactors=FALSE
)
write.csv(fixed,file.path(out_dir,"09_glmm_fixed_effects_or_v012.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

lrt <- anova(glmm_reduced,glmm,test="Chisq")
lrt_df <- data.frame(
  comparison="model_by_condition_interaction",
  df_difference=as.numeric(attr(logLik(glmm), "df") - attr(logLik(glmm_reduced), "df")),
  chisq=lrt$Chisq[2],
  p_value=lrt$`Pr(>Chisq)`[2],
  stringsAsFactors=FALSE
)
write.csv(lrt_df,file.path(out_dir,"10_glmm_interaction_lrt_v012.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

emm <- emmeans::emmeans(glmm, ~ condition_f | model_f)
emm_prob <- as.data.frame(summary(emm,type="response",infer=c(TRUE,TRUE)))
write.csv(emm_prob,file.path(out_dir,"11_glmm_estimated_marginal_probabilities_v012.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

ctr <- emmeans::contrast(
  emm,
  method=list(C1_vs_C0=c(-1,1)),
  by="model_f",
  adjust="none"
)
ctr_df <- as.data.frame(summary(ctr,type="response",infer=c(TRUE,TRUE),adjust="none"))
ctr_df$holm_p <- p.adjust(ctr_df$p.value,method="holm")
write.csv(ctr_df,file.path(out_dir,"12_glmm_model_specific_c1_vs_c0_v012.csv"),
          row.names=FALSE,fileEncoding="UTF-8")

# -----------------------
# 9. Pre-specified GEE fallback, only if GLMM unacceptable
# -----------------------
fallback_used <- FALSE
if (!glmm_ok) {
  fallback_used <- TRUE
  if (!requireNamespace("geepack", quietly=TRUE)) {
    writeLines("geepack", file.path(out_dir,"missing_R_packages_for_GEE_fallback_v012.txt"))
    stop("GLMM triggered the pre-specified fallback, but geepack is not installed.")
  }
  gee <- geepack::geeglm(
    initial_success_num ~ model_f * condition_f + category_f,
    id=task_f, data=gdat, family=binomial(link="logit"),
    corstr="exchangeable", std.err="san.se"
  )
  capture.output(summary(gee),file=file.path(out_dir,"13_gee_fallback_summary_v012.txt"))
  saveRDS(gee,file.path(out_dir,"13b_gee_fallback_model_v012.rds"))

  gc <- summary(gee)$coefficients
  gee_fixed <- data.frame(
    term=rownames(gc),
    estimate_log_odds=gc[,1],
    std_error=gc[,2],
    wald=gc[,3],
    p_value=gc[,4],
    odds_ratio=exp(gc[,1]),
    ci_low_or=exp(gc[,1]-1.96*gc[,2]),
    ci_high_or=exp(gc[,1]+1.96*gc[,2]),
    row.names=NULL,stringsAsFactors=FALSE
  )
  write.csv(gee_fixed,file.path(out_dir,"14_gee_fixed_effects_or_v012.csv"),
            row.names=FALSE,fileEncoding="UTF-8")

  beta <- coef(gee)
  V <- vcov(gee)
  int_idx <- grep("model_f.*:condition_f|condition_f.*:model_f",names(beta))
  if (length(int_idx)==2) {
    bb <- beta[int_idx]
    VV <- V[int_idx,int_idx,drop=FALSE]
    W <- as.numeric(t(bb) %*% solve(VV,bb))
    gee_int <- data.frame(
      comparison="model_by_condition_interaction",
      df=2, wald_chisq=W, p_value=pchisq(W,df=2,lower.tail=FALSE)
    )
    write.csv(gee_int,file.path(out_dir,"15_gee_interaction_wald_v012.csv"),
              row.names=FALSE,fileEncoding="UTF-8")
  }

  # Planned C1-vs-C0 contrasts under reference coding.
  nm <- names(beta)
  cond_name <- grep("^condition_fC1$",nm,value=TRUE)
  if (length(cond_name)==1) {
    gee_ctr <- data.frame()
    for (m in mods) {
      L <- rep(0,length(beta)); names(L)<-nm
      L[cond_name] <- 1
      if (m!="Qwen") {
        pat1 <- paste0("^model_f",m,":condition_fC1$")
        pat2 <- paste0("^condition_fC1:model_f",m,"$")
        hit <- grep(paste(pat1,pat2,sep="|"),nm,value=TRUE)
        if (length(hit)==1) L[hit] <- 1
      }
      est <- sum(L*beta)
      se <- sqrt(as.numeric(t(L)%*%V%*%L))
      z <- est/se
      p <- 2*pnorm(abs(z),lower.tail=FALSE)
      gee_ctr <- rbind(gee_ctr,data.frame(
        model=m, log_odds_ratio=est, std_error=se,
        odds_ratio=exp(est),
        ci_low_or=exp(est-1.96*se), ci_high_or=exp(est+1.96*se),
        z_value=z,p_value=p,stringsAsFactors=FALSE
      ))
    }
    gee_ctr$holm_p <- p.adjust(gee_ctr$p_value,method="holm")
    write.csv(gee_ctr,file.path(out_dir,"16_gee_model_specific_c1_vs_c0_v012.csv"),
              row.names=FALSE,fileEncoding="UTF-8")
  }
}

# -----------------------
# 10. Compact headline summary
# -----------------------
summary_lines <- c(
  "# Formal statistics V0.1.2 — compact summary",
  "",
  paste0("- Input SHA256: `",sha256_file(master_path),"`"),
  "- Benchmark: 90 tasks × 3 models × 2 conditions = 540 Initial observations",
  paste0("- Bootstrap: ",B," A–F-stratified task-level replicates"),
  paste0("- GLMM accepted as primary: **",glmm_ok,"**"),
  paste0("- GEE fallback used: **",fallback_used,"**"),
  "",
  "## Initial success (Wilson 95% CI)"
)
for (i in seq_len(nrow(initial_wilson))) {
  z <- initial_wilson[i,]
  summary_lines <- c(summary_lines,
    sprintf("- %s %s: %d/%d = %.1f%% (95%% CI %.1f–%.1f%%)",
            z$model,z$condition,z$success,z$n,z$percent,
            z$ci_low_percent,z$ci_high_percent))
}
summary_lines <- c(summary_lines,"","## Paired C1 − C0")
for (i in seq_len(nrow(paired_df))) {
  z <- paired_df[i,]
  bs <- boot_summary[boot_summary$model==z$model,]
  summary_lines <- c(summary_lines,
    sprintf("- %s: %+0.1f pp; FAIL→PASS=%d, PASS→FAIL=%d; bootstrap 95%% CI [%+0.1f, %+0.1f] pp; exact McNemar p=%g; Holm p=%g",
            z$model,z$risk_difference_pp,z$fail_to_pass,z$pass_to_fail,
            bs$percentile_ci_low_pp,bs$percentile_ci_high_pp,
            z$exact_mcnemar_p,z$holm_p))
}
summary_lines <- c(summary_lines,"","## Repair recovery conditional on Initial failure")
for (m in mods) {
  z <- repair_wilson[repair_wilson$model==m & repair_wilson$condition=="Overall",]
  summary_lines <- c(summary_lines,
    sprintf("- %s: %d/%d = %.1f%% (95%% CI %.1f–%.1f%%)",
            z$model,z$success,z$n,z$percent,z$ci_low_percent,z$ci_high_percent))
}
summary_lines <- c(summary_lines,"","## Model × condition interaction",
                   sprintf("- GLMM likelihood-ratio test: chi-square=%0.4f, df=%d, p=%g",
                           lrt_df$chisq,lrt_df$df_difference,lrt_df$p_value))
writeLines(summary_lines,file.path(out_dir,"00_formal_statistics_summary_v012.md"))

# -----------------------
# 11. Reproducibility manifest
# -----------------------
pkg_versions <- c(
  R=R.version.string,
  lme4=as.character(utils::packageVersion("lme4")),
  emmeans=as.character(utils::packageVersion("emmeans")),
  geepack=if (requireNamespace("geepack",quietly=TRUE))
    as.character(utils::packageVersion("geepack")) else "not installed"
)
manifest_lines <- c(
  paste("generated_utc",format(Sys.time(),tz="UTC",usetz=TRUE),sep="\t"),
  paste("input_csv",master_path,sep="\t"),
  paste("input_sha256",sha256_file(master_path),sep="\t"),
  paste("bootstrap_seed",20261001,sep="\t"),
  paste("bootstrap_replicates",B,sep="\t"),
  paste("glmm_accepted",glmm_ok,sep="\t"),
  paste("gee_fallback_used",fallback_used,sep="\t"),
  paste(names(pkg_versions),pkg_versions,sep="\t")
)
writeLines(manifest_lines,file.path(out_dir,"99_analysis_manifest_v012.tsv"))

cat("\nFormal statistics completed.\n")
cat("GLMM accepted:",glmm_ok,"\n")
cat("GEE fallback used:",fallback_used,"\n")
cat("Output:",out_dir,"\n")
